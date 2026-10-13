"""At-sea situational awareness dashboard (Bokeh server app).

Run from the parent directory with:
    conda activate muri-sea
    bokeh serve --show situational_awareness

Shows ship track over bathymetry (colored by an underway or ADCP variable,
with optional ADCP current vectors), ADCP u/v sections, underway time
series, and a current-conditions readout. Data are re-read every
config.REFRESH_S seconds.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cmocean
import numpy as np
import pandas as pd
from bokeh.io import curdoc
from bokeh.layouts import column, row
from bokeh.models import (Arrow, BoxAnnotation, Button, Checkbox, ColorBar,
                          ColumnDataSource, DatetimeTickFormatter, Div,
                          HoverTool, Label, LinearColorMapper, RadioButtonGroup,
                          Range1d, RangeSlider, Select, Slider, VeeHead)
from bokeh.plotting import figure
from matplotlib.colors import to_hex

import config as cfg
import readers

KN2MS = 0.514444


def cmo_palette(name, n=256):
    return [to_hex(c) for c in getattr(cmocean.cm, name)(np.linspace(0, 1, n))]


# Track color choices: label -> (source, column, units, palette)
COLOR_VARS = {
    "SST (SBE38)":          ("met", "ST", "°C", cmo_palette("thermal")),
    "TSG temperature":      ("met", "TT", "°C", cmo_palette("thermal")),
    "Salinity":             ("met", "SA", "psu", cmo_palette("haline")),
    "Fluorescence":         ("met", "FL", "µg/l", cmo_palette("algae")),
    "Ship speed (SOG)":     ("met", "SP", "kn", cmo_palette("speed")),
    "ADCP speed (z range)": ("adcp", None, "m/s", cmo_palette("speed")),
}
VECTOR_MODES = ["No vectors", "Shallowest bin", "Depth range"]
DATETIME_FMT = DatetimeTickFormatter(hours="%H:%M", minutes="%H:%M",
                                     hourmin="%H:%M", days="%m/%d %H:%M")
MS_PER_H = 3600e3
# busy: set while the app moves the sliders itself, so their callbacks
# don't respond to it
STATE = {"busy": False}


def ms2ts(ms):
    return pd.Timestamp(ms, unit="ms")


def ts2ms(t):
    return pd.Timestamp(t).value / 1e6


# ----------------------------------------------------------------- widgets
SLIDER_W = 520  # start, end and ADCP depth sliders are stacked at this width
# Start/end sliders hold ms since 1970 (naive UTC). Their bounds are set to
# the span of available data at every refresh; the titles print the times.
_t = ts2ms(pd.Timestamp.now(tz="UTC").tz_convert(None).floor("h"))
start_slide = Slider(title="Start", start=_t - cfg.WINDOW_MAX_H * MS_PER_H,
                     end=_t, step=cfg.WINDOW_STEP_MIN * 60e3,
                     value=_t - cfg.WINDOW_H * MS_PER_H, width=SLIDER_W,
                     show_value=False)
end_slide = Slider(title="End", start=start_slide.start, end=_t,
                   step=start_slide.step, value=_t, width=SLIDER_W,
                   show_value=False)
window_div = Div(text="", width=330)
# Down/up buttons to move each end of the window by cfg.NUDGE_MIN
nudge_btns = {(side, sign): Button(label="▲" if sign > 0 else "▼", width=36)
              for side in ("start", "end") for sign in (-1, 1)}


def nudge_group(side):
    """'-/+ N min' label over the down/up buttons for one end of the window."""
    return column(Div(text=f"-/+ {cfg.NUDGE_MIN:g} min",
                      styles={"font-size": "11px"}),
                  row(nudge_btns[side, -1], nudge_btns[side, 1]))


color_select = Select(title="Color track by", value=list(COLOR_VARS)[0],
                      options=list(COLOR_VARS), width=190)
zrange = RangeSlider(title="ADCP depth range (m)", start=0,
                     end=cfg.ADCP_ZMAX_PLOT, step=5, value=cfg.ADCP_ZRANGE,
                     width=SLIDER_W)
vec_mode = RadioButtonGroup(labels=VECTOR_MODES, active=2)
follow = Checkbox(label="Follow latest", active=True)
autozoom = Checkbox(label="Auto-zoom map", active=True)
status = Div(text="", width=650)
readout = Div(text="", width=720)

# ------------------------------------------------------------------ map
bathy_lon, bathy_lat, bathy_z = readers.load_bathy(cfg.BATHY_FILE,
                                                   cfg.BATHY_STRIDE)
lat0 = float(np.nanmean(bathy_lat))
bz = np.where(bathy_z < 0, bathy_z, np.nan).astype(float)

map_fig = figure(frame_width=650, frame_height=650,
                 x_range=Range1d(bathy_lon.min(), bathy_lon.max()),
                 y_range=Range1d(bathy_lat.min(), bathy_lat.max()),
                 tools="pan,wheel_zoom,box_zoom,reset,save",
                 active_scroll="wheel_zoom", x_axis_label="Longitude",
                 y_axis_label="Latitude", title="Ship track")
bathy_mapper = LinearColorMapper(palette=cmo_palette("deep"), low=-4500,
                                 high=0, nan_color="#e6dcc3")
bathy_mapper.palette = bathy_mapper.palette[::-1][40:]  # light shallow end
bathy_r = map_fig.image(image=[bz], x=bathy_lon.min(), y=bathy_lat.min(),
                        dw=np.ptp(bathy_lon), dh=np.ptp(bathy_lat),
                        color_mapper=bathy_mapper, alpha=0.75)
map_fig.contour(bathy_lon, bathy_lat, np.nan_to_num(bathy_z, nan=1),
                levels=sorted(cfg.ISOBATHS), line_color="#5a6e80", line_alpha=0.5,
                line_width=0.7)
map_fig.contour(bathy_lon, bathy_lat, np.nan_to_num(bathy_z, nan=1),
                levels=[0], line_color="black", line_width=1.2)
map_fig.add_tools(HoverTool(renderers=[bathy_r],
                            tooltips=[("depth", "@image{0} m")]))

track_mapper = LinearColorMapper(palette=COLOR_VARS[color_select.value][3])
line_src = ColumnDataSource(dict(lon=[], lat=[]))
track_src = ColumnDataSource(dict(lon=[], lat=[], t=[], val=[]))
ends_src = ColumnDataSource(dict(lon=[], lat=[], t=[], color=[], label=[]))
now_src = ColumnDataSource(dict(lon=[], lat=[], t=[], label=[]))
vec_src = ColumnDataSource(dict(x0=[], y0=[], x1=[], y1=[]))
vscale_src = ColumnDataSource(dict(x0=[], y0=[], x1=[], y1=[]))

map_fig.line("lon", "lat", source=line_src, color="gray", line_width=1)
track_r = map_fig.scatter("lon", "lat", source=track_src, size=6,
                          color={"field": "val", "transform": track_mapper},
                          line_color=None)
map_fig.add_layout(Arrow(source=vec_src, x_start="x0", y_start="y0",
                         x_end="x1", y_end="y1", line_color="black",
                         line_width=1.5,
                         end=VeeHead(size=6, line_color="black")))
map_fig.add_layout(Arrow(source=vscale_src, x_start="x0", y_start="y0",
                         x_end="x1", y_end="y1", line_color="black",
                         line_width=2, end=VeeHead(size=8)))
vscale_label = Label(x=0, y=0, text="", text_font_size="11px",
                     x_offset=4, y_offset=4, background_fill_color="white",
                     background_fill_alpha=0.7)
map_fig.add_layout(vscale_label)
# Window start/end (circles) and the ship's current position (x). The x is
# drawn larger and on top so it stays visible when it sits on the end circle.
ends_r = map_fig.scatter("lon", "lat", source=ends_src, size=12,
                         fill_color="color", line_color="black",
                         legend_field="label")
now_r = map_fig.scatter("lon", "lat", source=now_src, marker="x", size=20,
                        line_color="red", line_width=3,
                        legend_label="Current position")
map_fig.legend.location = "top_right"
map_fig.legend.label_text_font_size = "10px"
map_fig.legend.padding = 3
map_fig.legend.background_fill_alpha = 0.7
map_fig.add_tools(HoverTool(renderers=[ends_r, now_r], tooltips=[
    ("", "@label"), ("time", "@t{%m/%d %H:%M}"),
    ("pos", "@lat{0.0000}, @lon{0.0000}")], formatters={"@t": "datetime"}))
map_fig.add_tools(HoverTool(renderers=[track_r], tooltips=[
    ("time", "@t{%m/%d %H:%M}"), ("value", "@val{0.000}"),
    ("pos", "@lat{0.0000}, @lon{0.0000}")], formatters={"@t": "datetime"}))
track_cbar = ColorBar(color_mapper=track_mapper, width=12,
                      title=color_select.value)
map_fig.add_layout(track_cbar, "right")

# ----------------------------------------------------- ADCP u, v sections
time_range = Range1d(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02"))
uv_mapper = LinearColorMapper(palette=cmo_palette("balance"), low=-0.5,
                              high=0.5, nan_color="#dddddd")
uv_srcs, uv_bands, uv_figs = {}, {}, {}
for comp in ("u", "v"):
    f = figure(width=720, height=190, x_axis_type="datetime",
               x_range=time_range,
               y_range=Range1d(cfg.ADCP_ZMAX_PLOT, 0),
               tools="xpan,xwheel_zoom,reset,save", y_axis_label="Depth (m)",
               title=f"ADCP {cfg.SONAR} {comp} (m/s, "
                     f"{'east' if comp == 'u' else 'north'})")
    f.xaxis.formatter = DATETIME_FMT
    uv_srcs[comp] = ColumnDataSource(dict(image=[], x=[], y=[], dw=[], dh=[]))
    r = f.image("image", x="x", y="y", dw="dw", dh="dh",
                source=uv_srcs[comp], color_mapper=uv_mapper)
    f.add_tools(HoverTool(renderers=[r], tooltips=[(comp, "@image{0.00}")]))
    uv_bands[comp] = BoxAnnotation(top=cfg.ADCP_ZRANGE[0],
                                   bottom=cfg.ADCP_ZRANGE[1],
                                   fill_alpha=0, line_color="black",
                                   line_dash="dashed", line_width=1)
    f.add_layout(uv_bands[comp])
    f.add_layout(ColorBar(color_mapper=uv_mapper, width=10), "right")
    uv_figs[comp] = f

# --------------------------------------------------- underway time series
ts_src = ColumnDataSource(dict(t=[], ST=[], TT=[], SA=[], TW=[], TI=[],
                               SP=[]))
ts_specs = [  # (title, [(column, color, legend)])
    ("Temperature (°C)", [("ST", "#c0392b", "SST"), ("TT", "#2c7fb8", "TSG")]),
    ("Salinity (psu)", [("SA", "#1b9e77", None)]),
    ("True wind (m/s)", [("TW", "#555555", None)]),
    ("Ship speed (kn)", [("SP", "#7570b3", None)]),
]
ts_figs = []
for title, lines in ts_specs:
    f = figure(width=720, height=120, x_axis_type="datetime",
               x_range=time_range, tools="xpan,xwheel_zoom,reset,save",
               title=title)
    f.xaxis.formatter = DATETIME_FMT
    rs = []
    for col, color, leg in lines:
        kw = dict(legend_label=leg) if leg else {}
        rs.append(f.line("t", col, source=ts_src, color=color, **kw))
    if any(leg for _, _, leg in lines):
        f.legend.location = "top_left"
        f.legend.label_text_font_size = "9px"
        f.legend.padding = 2
    f.add_tools(HoverTool(renderers=rs[:1], mode="vline", tooltips=[
        ("time", "@t{%H:%M:%S}")] + [(c, f"@{c}{{0.000}}") for c, _, _ in lines],
        formatters={"@t": "datetime"}))
    ts_figs.append(f)


# ---------------------------------------------------------------- helpers
def robust_limits(x, lo=2, hi=98):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return 0.0, 1.0
    a, b = np.percentile(x, [lo, hi])
    if b - a < 1e-6:
        a, b = a - 0.05, b + 0.05
    return float(a), float(b)


def set_map_ranges(lon, lat):
    """Zoom the map to the track with a cos(lat)-correct aspect ratio."""
    lon, lat = np.asarray(lon), np.asarray(lat)
    ok = np.isfinite(lon) & np.isfinite(lat)
    if not ok.any():
        return
    lon, lat = lon[ok], lat[ok]
    clat = 0.5 * (lat.min() + lat.max())
    clon = 0.5 * (lon.min() + lon.max())
    coslat = np.cos(np.radians(clat))
    half_y = max(0.6 * np.ptp(lat), 0.6 * np.ptp(lon) * coslat, 0.03)
    half_x = half_y / coslat * map_fig.frame_width / map_fig.frame_height
    map_fig.x_range.update(start=clon - half_x, end=clon + half_x)
    map_fig.y_range.update(start=clat - half_y, end=clat + half_y)


def ddm(x, pos, neg):
    hemi = pos if x >= 0 else neg
    x = abs(x)
    return f"{int(x)}° {60 * (x % 1):06.3f}' {hemi}"


def adcp_image(ds, comp):
    """Regularly gridded (depth x time) image of u or v for bokeh.image."""
    z = readers.bin_depths(ds)
    keep = z <= cfg.ADCP_ZMAX_PLOT
    zdim = [d for d in ds[comp].dims if d != "time"][0]
    t = ds["time"].values
    dt = np.median(np.diff(t)) if t.size > 1 else np.timedelta64(180, "s")
    grid = np.arange(t[0], t[-1] + dt / 2, dt)
    da = ds[comp].isel({zdim: keep}).transpose(zdim, "time")
    da = da.reindex(time=grid, method="nearest", tolerance=dt / 2)
    zk = z[keep]
    dz = np.median(np.diff(zk)) if zk.size > 1 else 8.0
    ms = dt / np.timedelta64(1, "ms")
    return dict(image=[da.values.astype(float)],
                x=[pd.Timestamp(grid[0]).value / 1e6 - ms / 2],
                y=[zk[0] - dz / 2], dw=[ms * grid.size], dh=[dz * zk.size])


def age_html(label, t_last, now, stale_min):
    if t_last is None:
        return f"<span style='color:#c0392b'>{label}: no data</span>"
    age = (now - t_last).total_seconds() / 60
    color = "#c0392b" if age > stale_min else "#2e7d32"
    return (f"<span style='color:{color}'>{label}: {t_last:%H:%M:%S} "
            f"({age:.0f} min old)</span>")


# ---------------------------------------------------------- time window
def set_slider_bounds(t_first, t_last):
    """Let the start/end sliders span the available data."""
    lo = ts2ms(t_first)
    hi = max(ts2ms(t_last), lo + cfg.WINDOW_MIN_H * MS_PER_H)
    for s in (start_slide, end_slide):
        s.update(start=lo, end=hi)


def clamp_window(t0, t1, moved):
    """Keep [t0, t1] (ms) inside the slider bounds and between WINDOW_MIN_H
    and WINDOW_MAX_H long, moving the side that the user did not move."""
    lo, hi = start_slide.start, start_slide.end
    wmin, wmax = cfg.WINDOW_MIN_H * MS_PER_H, cfg.WINDOW_MAX_H * MS_PER_H
    t0, t1 = min(max(t0, lo), hi), min(max(t1, lo), hi)
    if moved == "start":
        t1 = min(max(t1, t0 + wmin), t0 + wmax, hi)
        t0 = min(t0, t1 - wmin)
    else:
        t0 = max(min(t0, t1 - wmin), t1 - wmax, lo)
        t1 = max(t1, t0 + wmin)
    return t0, t1


def set_window(t0, t1):
    """Move the sliders to [t0, t1] (ms) and print the chosen times."""
    STATE["busy"] = True
    try:
        if start_slide.value != t0:
            start_slide.value = t0
        if end_slide.value != t1:
            end_slide.value = t1
    finally:
        STATE["busy"] = False
    start_slide.title = f"Start: {ms2ts(t0):%Y-%m-%d %H:%M} UTC"
    end_slide.title = f"End: {ms2ts(t1):%Y-%m-%d %H:%M} UTC"
    window_div.text = (f"Window: <b>{(t1 - t0) / MS_PER_H:.2f} h</b> "
                       f"(max {cfg.WINDOW_MAX_H:g} h)")


# ----------------------------------------------------------------- update
def update():
    now = pd.Timestamp.now(tz="UTC").tz_convert(None)
    warnings = []
    zmin, zmax = zrange.value

    t_met_first = t_met_last = t_adcp_first = t_adcp_last = None
    try:
        t_met_first = readers.earliest_met_time(cfg.MET_DIR)
        t_met_last = readers.latest_met_time(cfg.MET_DIR)
    except Exception as e:
        warnings.append(f"MET: {e}")
    try:
        ds_all = readers.read_adcp(cfg.ADCP_FILE)
        t_adcp_first = pd.Timestamp(ds_all["time"].values[0])
        t_adcp_last = pd.Timestamp(ds_all["time"].values[-1])
    except Exception as e:
        ds_all = None
        warnings.append(f"ADCP: {e}")

    # --- time window from the start/end sliders
    firsts = [t for t in (t_met_first, t_adcp_first) if t is not None]
    lasts = [t for t in (t_met_last, t_adcp_last) if t is not None]
    t_last = max(lasts) if lasts else now
    t_first = min(firsts) if firsts else t_last - pd.Timedelta(
        hours=cfg.WINDOW_MAX_H)
    t0, t1 = start_slide.value, end_slide.value
    set_slider_bounds(t_first, t_last)
    if follow.active:  # keep the window length, end at the latest data
        t0, t1 = end_slide.end - (t1 - t0), end_slide.end
    set_window(*clamp_window(t0, t1, "end"))
    t0, t1 = ms2ts(start_slide.value), ms2ts(end_slide.value)

    # --- underway
    met = pd.DataFrame()
    try:
        met = readers.read_met(cfg.MET_DIR, t0, t1)
    except Exception as e:
        warnings.append(f"MET: {e}")
    if len(met):
        line_src.data = dict(lon=met["LO"].values, lat=met["LA"].values)
        ts_src.data = {"t": met.index.values,
                       **{c: met[c].values if c in met else
                          np.full(len(met), np.nan)
                          for c in ("ST", "TT", "SA", "TW", "TI", "SP")}}
        pos = met[["LO", "LA"]].dropna()
        ends = pos.iloc[[0, -1]] if len(pos) else pos
        ends_src.data = dict(lon=ends["LO"].values, lat=ends["LA"].values,
                             t=ends.index.values,
                             color=["#2c7fb8", "#c0392b"][:len(ends)],
                             label=["Window start", "Window end"][:len(ends)])
    else:
        line_src.data = dict(lon=[], lat=[])
        ts_src.data = {k: [] for k in ts_src.data}
        ends_src.data = dict(lon=[], lat=[], t=[], color=[], label=[])
    now_src.data = dict(lon=[], lat=[], t=[], label=[])
    if t_met_last is not None:
        try:
            pos = readers.read_met(cfg.MET_DIR,
                                   t_met_last - pd.Timedelta(minutes=10),
                                   t_met_last)[["LO", "LA"]].dropna()
            if len(pos):
                now_src.data = dict(lon=pos["LO"].values[-1:],
                                    lat=pos["LA"].values[-1:],
                                    t=pos.index.values[-1:],
                                    label=["Current position"])
        except Exception as e:
            warnings.append(f"MET: {e}")
    time_range.update(start=t0, end=t1)

    # --- ADCP
    ds = None
    if ds_all is not None:
        ds = ds_all.sel(time=slice(t0, t1))
        if ds.sizes["time"] == 0:
            ds = None
    if ds is not None:
        ubar, vbar = readers.depth_avg(ds, zmin, zmax)
        imgs = {c: adcp_image(ds, c) for c in ("u", "v")}
        for c in ("u", "v"):
            uv_srcs[c].data = imgs[c]
        lim = max(0.2, robust_limits(np.abs(np.concatenate(
            [imgs["u"]["image"][0].ravel(), imgs["v"]["image"][0].ravel()])),
            0, 98)[1])
        lim = np.ceil(lim * 10) / 10
        uv_mapper.update(low=-lim, high=lim)
    else:
        ubar = vbar = np.array([])
        for c in ("u", "v"):
            uv_srcs[c].data = dict(image=[], x=[], y=[], dw=[], dh=[])
    for band in uv_bands.values():
        band.update(top=zmin, bottom=zmax)

    # --- track color
    src_kind, col, units, palette = COLOR_VARS[color_select.value]
    if src_kind == "met" and len(met) and col in met:
        tr = dict(lon=met["LO"].values, lat=met["LA"].values,
                  t=met.index.values, val=met[col].values)
    elif src_kind == "adcp" and ds is not None:
        tr = dict(lon=ds["lon"].values, lat=ds["lat"].values,
                  t=ds["time"].values, val=np.hypot(ubar, vbar))
    else:
        tr = dict(lon=[], lat=[], t=[], val=[])
    track_src.data = tr
    lo, hi = robust_limits(tr["val"])
    track_mapper.update(palette=palette, low=lo, high=hi)
    track_cbar.title = (f"{color_select.value} ({units})"
                        if src_kind == "met" else
                        f"ADCP speed {zmin:.0f}-{zmax:.0f} m ({units})")

    # --- map extent
    if autozoom.active:
        set_map_ranges(np.r_[line_src.data["lon"], tr["lon"]],
                       np.r_[line_src.data["lat"], tr["lat"]])

    # --- ADCP vectors
    mode = VECTOR_MODES[vec_mode.active]
    if ds is not None and mode != "No vectors":
        if mode == "Shallowest bin":
            uu, vv = readers.surface_bin(ds)
        else:
            uu, vv = ubar, vbar
        df = pd.DataFrame(dict(lon=ds["lon"].values, lat=ds["lat"].values,
                               u=uu, v=vv))
        step = int(np.ceil(len(df) / cfg.MAX_VECTORS))
        df = df.groupby(np.arange(len(df)) // step).mean().dropna()
        yspan = map_fig.y_range.end - map_fig.y_range.start
        s = 0.15 * yspan  # degrees of latitude per 1 m/s
        coslat = np.cos(np.radians(df["lat"].mean() if len(df) else lat0))
        vec_src.data = dict(x0=df["lon"].values, y0=df["lat"].values,
                            x1=(df["lon"] + df["u"] * s / coslat).values,
                            y1=(df["lat"] + df["v"] * s).values)
        xs = map_fig.x_range.start + 0.05 * (map_fig.x_range.end
                                             - map_fig.x_range.start)
        ys = map_fig.y_range.start + 0.05 * yspan
        vscale_src.data = dict(x0=[xs], y0=[ys], x1=[xs + 0.5 * s / coslat],
                               y1=[ys])
        vscale_label.update(x=xs, y=ys, text="0.5 m/s")
    else:
        vec_src.data = dict(x0=[], y0=[], x1=[], y1=[])
        vscale_src.data = dict(x0=[], y0=[], x1=[], y1=[])
        vscale_label.text = ""

    # --- readout
    rows = []
    if len(met):
        last = met.iloc[-1]
        g = last.get
        rows += [
            ("Time (UTC)", f"{met.index[-1]:%Y-%m-%d %H:%M:%S}"),
            ("Position", f"{g('LA'):.5f}, {g('LO'):.5f} &nbsp; "
                         f"({ddm(g('LA'), 'N', 'S')}, {ddm(g('LO'), 'E', 'W')})"),
            ("SOG / COG", f"{g('SP'):.1f} kn / {g('CR'):.0f}°"),
            ("SST / TSG T / S", f"{g('ST'):.3f} °C / {g('TT'):.3f} °C / "
                                f"{g('SA'):.3f} psu"),
            ("True wind", f"{g('TW'):.1f} m/s ({g('TW') / KN2MS:.0f} kn) "
                          f"from {g('TI'):.0f}°"),
        ]
        if "MB" in met and np.isfinite(g("MB")):
            rows.append(("Water depth (multibeam)", f"{g('MB'):.0f} m"))
    if ds is not None and np.isfinite(ubar).any():
        i = np.where(np.isfinite(ubar))[-1][-1]
        spd = np.hypot(ubar[i], vbar[i])
        toward = np.degrees(np.arctan2(ubar[i], vbar[i])) % 360
        rows.append((f"Current {zmin:.0f}-{zmax:.0f} m",
                     f"{spd:.2f} m/s ({spd / KN2MS:.1f} kn) toward "
                     f"{toward:.0f}° &nbsp; [u={ubar[i]:+.2f}, "
                     f"v={vbar[i]:+.2f}]"))
    table = "".join(f"<tr><td style='padding-right:12px'><b>{k}</b></td>"
                    f"<td>{v}</td></tr>" for k, v in rows)
    readout.text = (f"<table style='font-size:13px'>{table}</table>"
                    f"<div style='font-size:12px'>"
                    f"{age_html('MET', t_met_last, now, cfg.MET_STALE_MIN)}"
                    f" &nbsp;|&nbsp; "
                    f"{age_html('ADCP', t_adcp_last, now, cfg.ADCP_STALE_MIN)}"
                    f"</div>")

    msg = (f"<b>{cfg.CRUISE}</b> &nbsp; last refresh {now:%H:%M:%S} UTC "
           f"(every {cfg.REFRESH_S} s) &nbsp; window {t0:%m/%d %H:%M} to "
           f"{t1:%m/%d %H:%M}")
    if warnings:
        msg += ("<br><span style='color:#c0392b'>"
                + "<br>".join(warnings) + "</span>")
    status.text = msg


def on_change(attr, old, new):
    update()


def on_drag(moved):
    """While a slider is dragged, enforce the window limits and print times."""
    def cb(attr, old, new):
        if not STATE["busy"]:
            set_window(*clamp_window(start_slide.value, end_slide.value,
                                     moved))
    return cb


def on_end_release(attr, old, new):
    # Moving the end back in time stops following; to the end resumes it
    at_latest = end_slide.value >= end_slide.end - end_slide.step / 2
    if follow.active != at_latest:
        follow.active = at_latest  # its callback runs update()
    else:
        update()


def on_nudge(side, sign):
    """Move one end of the window by NUDGE_MIN, then redraw."""
    def cb():
        t0, t1 = start_slide.value, end_slide.value
        dt = sign * cfg.NUDGE_MIN * 60e3
        if side == "start":
            set_window(*clamp_window(t0 + dt, t1, "start"))
            update()
        else:
            set_window(*clamp_window(t0, t1 + dt, "end"))
            on_end_release(None, None, None)
    return cb


for (side, sign), btn in nudge_btns.items():
    btn.on_click(on_nudge(side, sign))
start_slide.on_change("value", on_drag("start"))
end_slide.on_change("value", on_drag("end"))
start_slide.on_change("value_throttled", on_change)
end_slide.on_change("value_throttled", on_end_release)
for w, attr in [(color_select, "value"),
                (zrange, "value_throttled"), (vec_mode, "active"),
                (follow, "active"), (autozoom, "active")]:
    w.on_change(attr, on_change)

controls = column(
    status,
    row(start_slide, nudge_group("start")),
    row(end_slide, nudge_group("end")),
    zrange,
    row(window_div, follow, autozoom),
    row(color_select, column(Div(text="&nbsp;", height=12), vec_mode)),
)
layout = row(column(controls, map_fig),
             column(readout, uv_figs["u"], uv_figs["v"], *ts_figs))

update()
doc = curdoc()
doc.title = f"{cfg.CRUISE} situational awareness"
doc.add_root(layout)
doc.add_periodic_callback(update, cfg.REFRESH_S * 1000)
