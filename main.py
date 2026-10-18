"""At-sea situational awareness dashboard (Bokeh server app).

Run from the parent directory with:
    conda activate muri-sea
    bokeh serve --show situational_awareness

Shows ship track over bathymetry or satellite SST (colored by an underway or ADCP variable,
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
from bokeh.events import DoubleTap, Tap
from bokeh.io import curdoc
from bokeh.layouts import column, row
from bokeh.models import (Arrow, BoxAnnotation, Button, Checkbox, ColorBar,
                          ColumnDataSource, CustomJSHover,
                          DatetimeTickFormatter, Div,
                          HoverTool, Label, Legend, LegendItem,
                          LinearColorMapper, RadioButtonGroup,
                          Range1d, RangeSlider, Select, Slider, Span,
                          Toggle, VeeHead)
from bokeh.plotting import figure
from matplotlib.colors import to_hex

import config as cfg
import readers

KN2MS = 0.514444


def cmo_palette(name, n=256):
    return [to_hex(c) for c in getattr(cmocean.cm, name)(np.linspace(0, 1, n))]


# Track color choices: label -> (source, column, units, palette)
COLOR_VARS = {
    "SST (underway)":       ("met", "sst", "°C", cmo_palette("thermal")),
    "TSG temperature":      ("met", "tsg_t", "°C", cmo_palette("thermal")),
    "Salinity":             ("met", "sal", "psu", cmo_palette("haline")),
    "Fluorescence":         ("met", "fluor", "µg/l", cmo_palette("algae")),
    "Ship speed (SOG)":     ("met", "sog", "kn", cmo_palette("speed")),
    "ADCP speed (z range)": ("adcp", None, "m/s", cmo_palette("speed")),
}
VECTOR_MODES = ["No vectors", "Shallowest bin", "Depth range"]
DATETIME_FMT = DatetimeTickFormatter(hours="%H:%M", minutes="%H:%M",
                                     hourmin="%H:%M", days="%m/%d %H:%M")
MS_PER_H = 3600e3
# Hover rows for positions: decimal degrees and degrees-minutes, e.g.
# 35.46794 (35° 28.076' N). Longitudes are shown in -180 to 180.
_DDM_JS = """
    const a = Math.abs(v), d = Math.floor(a);
    const m = ((a - d) * 60).toFixed(3).padStart(6, "0");
    return v.toFixed(5) + " (" + d + "\u00b0 " + m + "' " + (v >= 0 ? P : N) + ")";
"""
LAT_FMT = CustomJSHover(code='const v = value, P = "N", N = "S";' + _DDM_JS)
LON_FMT = CustomJSHover(code='const v = ((value + 180) % 360 + 360) % 360'
                        ' - 180, P = "E", N = "W";' + _DDM_JS)
POS_TOOLTIPS = [("lat", "@lat{ddm}"), ("lon", "@lon{ddm}")]
HOVER_FMT = {"@t": "datetime", "@lat": LAT_FMT, "@lon": LON_FMT}
# Same for the cursor position over the map backgrounds
CURSOR_TOOLTIPS = [("lat", "$y{ddm}"), ("lon", "$x{ddm}")]
CURSOR_FMT = {"$y": LAT_FMT, "$x": LON_FMT}
# busy: set while the app moves the sliders itself, so their callbacks
# don't respond to it
# zoomed: the map has been fitted to the track once, after which it keeps
# the user's zoom unless Auto-zoom is on
# dist: (lon, lat) points clicked with Calculate distance on
# mark_t: time of the marker placed by clicking a plot (or None)
# met: underway data in the window, for placing the marker
STATE = {"busy": False, "zoomed": False, "mark_t": None,
         "met": pd.DataFrame(), "dist": []}


def ms2ts(ms):
    return pd.Timestamp(ms, unit="ms")


def ts2ms(t):
    return pd.Timestamp(t).value / 1e6


def wrap_lon(lon):
    """Longitudes in the map's convention: 0-360 if cfg.LON_0_360 (cruises
    across 180°), else -180 to 180."""
    lon = np.asarray(lon, float)
    return lon % 360 if cfg.LON_0_360 else (lon + 180) % 360 - 180


def lon180(lon):
    """Longitude in -180 to 180, for printing."""
    return (lon + 180) % 360 - 180


MET_KW = dict(pattern=cfg.MET_FILE, missing=cfg.MET_MISSING,
              columns=cfg.MET_COLUMNS)


def read_met(t0, t1):
    met = readers.read_met(cfg.MET_DIR, t0, t1, **MET_KW)
    if "lon" in met:
        met = met.assign(lon=wrap_lon(met["lon"]))
    return met


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
ZPLOT0 = cfg.ADCP_ZMAX_PLOT or 400  # until the ADCP file says how deep
zrange = RangeSlider(title="ADCP depth range (m)", start=0,
                     end=ZPLOT0, step=5, value=cfg.ADCP_ZRANGE,
                     width=SLIDER_W)
vec_mode = RadioButtonGroup(labels=VECTOR_MODES, active=2)
BASEMAPS = ["Bathymetry", "Satellite SST"]
basemap = RadioButtonGroup(labels=BASEMAPS, active=0)
sonar_btn = RadioButtonGroup(labels=cfg.SONARS, active=0)
follow = Checkbox(label="Follow latest", active=True)
autozoom = Checkbox(label="Auto-zoom map", active=False)
zoom_btn = Button(label="Zoom to track", width=110)
MARK_COLOR = "#ff7f00"
mark_div = Div(text="", width=650)
DIST_COLOR = "#6a3d9a"
dist_btn = Toggle(label="Calculate distance", width=140)
dist_div = Div(text="", width=500)
status = Div(text="", width=650)
readout = Div(text="", width=720)

# ------------------------------------------------------------------ map
if cfg.BATHY_FILE is not None:
    bathy_lon, bathy_lat, bathy_z = readers.load_bathy(cfg.BATHY_FILE,
                                                       cfg.BATHY_STRIDE)
    bathy_lon = wrap_lon(bathy_lon)
    order = np.argsort(bathy_lon)
    bathy_lon, bathy_z = bathy_lon[order], bathy_z[:, order]
    # land keeps its elevation (drawn in the mapper's high_color) so the
    # hover can say "land"; NaN would skip the hover formatter
    bz = bathy_z.astype(float)
    xr0, yr0 = (bathy_lon.min(), bathy_lon.max()), (bathy_lat.min(),
                                                     bathy_lat.max())
else:  # auto-zoom finds the ship at the first refresh
    xr0, yr0 = (-180, 180), (-90, 90)

map_fig = figure(frame_width=650, frame_height=650,
                 x_range=Range1d(*xr0), y_range=Range1d(*yr0),
                 tools="pan,wheel_zoom,box_zoom,reset,save",
                 active_scroll="wheel_zoom", x_axis_label="Longitude",
                 y_axis_label="Latitude", title="Ship track")
bathy_mapper = LinearColorMapper(palette=cmo_palette("deep"), low=-4500,
                                 high=0, high_color="#e6dcc3",
                                 nan_color="#e6dcc3")
bathy_mapper.palette = bathy_mapper.palette[::-1][40:]  # light shallow end
bathy_src = ColumnDataSource(dict(image=[], x=[], y=[], dw=[], dh=[]))
if cfg.BATHY_FILE is not None:
    bathy_src.data = dict(image=[bz], x=[bathy_lon.min()],
                          y=[bathy_lat.min()], dw=[np.ptp(bathy_lon)],
                          dh=[np.ptp(bathy_lat)])
bathy_r = map_fig.image("image", x="x", y="y", dw="dw", dh="dh",
                        source=bathy_src, color_mapper=bathy_mapper,
                        alpha=0.75)
# Satellite SST basemap, shown instead of the bathymetry when picked
sst_mapper = LinearColorMapper(palette=cmo_palette("thermal"), low=10,
                               high=20, nan_color="#e6dcc3")
sst_src = ColumnDataSource(dict(image=[], x=[], y=[], dw=[], dh=[]))
sst_r = map_fig.image("image", x="x", y="y", dw="dw", dh="dh",
                      source=sst_src, color_mapper=sst_mapper, visible=False)
map_fig.add_tools(HoverTool(renderers=[sst_r],
                            tooltips=[("satellite SST", "@image{0.00} °C"),
                                      *CURSOR_TOOLTIPS],
                            formatters=CURSOR_FMT))
if cfg.BATHY_FILE is not None:
    map_fig.contour(bathy_lon, bathy_lat, np.nan_to_num(bathy_z, nan=1),
                    levels=sorted(cfg.ISOBATHS), line_color="#5a6e80",
                    line_alpha=0.5, line_width=0.7)
    map_fig.contour(bathy_lon, bathy_lat, np.nan_to_num(bathy_z, nan=1),
                    levels=[0], line_color="black", line_width=1.2)
map_fig.add_tools(HoverTool(renderers=[bathy_r],
                            tooltips=[("depth", "@image{depth}"),
                                      *CURSOR_TOOLTIPS],
                            formatters={"@image": CustomJSHover(code=(
                                'return value >= 0 ? "land" : '
                                '(-value).toFixed(0) + " m";')),
                                **CURSOR_FMT}))

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
# Two points clicked with Calculate distance on, and the line between them
dist_src = ColumnDataSource(dict(lon=[], lat=[]))
map_fig.line("lon", "lat", source=dist_src, color=DIST_COLOR, line_width=2,
             line_dash="dashed")
map_fig.scatter("lon", "lat", source=dist_src, marker="circle_cross",
                size=12, fill_alpha=0, line_color=DIST_COLOR, line_width=2)
# Marker placed by clicking a time series or the map
mark_src = ColumnDataSource(dict(lon=[], lat=[]))
map_fig.scatter("lon", "lat", source=mark_src, marker="diamond", size=16,
                fill_color=MARK_COLOR, line_color="black", line_width=1.5,
                legend_label="Marker")
# Tracked items and stations go in a legend below the map, the window
# start/end, current position and marker in the one on the map
below_items = []
# Fixes of each cfg.TRACKED item (moorings, drifters, ...) in the window
tracked_srcs = []
for item in cfg.TRACKED:
    src = ColumnDataSource(dict(lon=[], lat=[], t=[]))
    r = map_fig.scatter("lon", "lat", source=src, size=5,
                        marker=item.get("marker", "circle"),
                        color=item.get("color", "red"), line_color=None)
    below_items.append(LegendItem(label=item["name"], renderers=[r]))
    map_fig.add_tools(HoverTool(renderers=[r], tooltips=[
        ("", item["name"]), ("time", "@t{%m/%d %H:%M:%S}"),
        *POS_TOOLTIPS], formatters=HOVER_FMT))
    tracked_srcs.append(src)
# Fixed positions from cfg.STATIONS (moorings, stations, ...)
if cfg.STATIONS:
    st = cfg.STATIONS
    stations_src = ColumnDataSource(dict(
        name=[s["name"] for s in st], lat=[s["lat"] for s in st],
        lon=list(wrap_lon([s["lon"] for s in st])),
        marker=[s.get("marker", "square") for s in st],
        color=[s.get("color", "black") for s in st]))
    stations_r = map_fig.scatter("lon", "lat", source=stations_src, size=13,
                                 marker="marker", fill_color="color",
                                 line_color="black", line_width=1)
    below_items.append(LegendItem(label={"field": "name"},
                                  renderers=[stations_r]))
    map_fig.add_tools(HoverTool(renderers=[stations_r], tooltips=[
        ("", "@name"), *POS_TOOLTIPS], formatters=HOVER_FMT))
map_fig.legend.location = "top_right"
map_fig.legend.label_text_font_size = "10px"
map_fig.legend.padding = 3
map_fig.legend.background_fill_alpha = 0.7
if below_items:
    map_fig.add_layout(Legend(items=below_items, orientation="horizontal",
                              label_text_font_size="10px", padding=3,
                              spacing=12, border_line_color=None), "below")
map_fig.add_tools(HoverTool(renderers=[ends_r, now_r], tooltips=[
    ("", "@label"), ("time", "@t{%m/%d %H:%M:%S}"),
    *POS_TOOLTIPS], formatters=HOVER_FMT))
map_fig.add_tools(HoverTool(renderers=[track_r], tooltips=[
    ("time", "@t{%m/%d %H:%M:%S}"), ("value", "@val{0.000}"),
    *POS_TOOLTIPS], formatters=HOVER_FMT))
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
               y_range=Range1d(ZPLOT0, 0),
               tools="xpan,xwheel_zoom,reset,save", y_axis_label="Depth (m)",
               title=f"ADCP {comp}")
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
    ("Temperature (°C)", [("sst", "#c0392b", "SST"), ("tsg_t", "#2c7fb8", "TSG")]),
    ("Salinity (psu)", [("sal", "#1b9e77", None)]),
    ("True wind (m/s)", [("wind", "#555555", None)]),
    ("Ship speed (kn)", [("sog", "#7570b3", None)]),
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

# The marker's time as a vertical line on every time axis
time_figs = [uv_figs["u"], uv_figs["v"], *ts_figs]
mark_spans = []
for f in time_figs:
    sp = Span(location=0, dimension="height", line_color=MARK_COLOR,
              line_width=2, line_dash="dashed", visible=False)
    f.add_layout(sp)
    mark_spans.append(sp)


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
    """Zoom the map to the track with a cos(lat)-correct aspect ratio.
    Returns False if there was nothing to zoom to."""
    lon, lat = np.asarray(lon), np.asarray(lat)
    ok = np.isfinite(lon) & np.isfinite(lat)
    if not ok.any():
        return False
    lon, lat = lon[ok], lat[ok]
    clat = 0.5 * (lat.min() + lat.max())
    clon = 0.5 * (lon.min() + lon.max())
    coslat = np.cos(np.radians(clat))
    half_y = max(0.6 * np.ptp(lat), 0.6 * np.ptp(lon) * coslat, 0.03)
    half_x = half_y / coslat * map_fig.frame_width / map_fig.frame_height
    map_fig.x_range.update(start=clon - half_x, end=clon + half_x)
    map_fig.y_range.update(start=clat - half_y, end=clat + half_y)
    return True


# ---------------------------------------------------------------- marker
def draw_mark():
    """Show the marker at STATE["mark_t"]: a line on the time axes and a
    diamond on the map at the nearest underway fix. Hidden if it is
    unset or outside the time window."""
    t, met = STATE["mark_t"], STATE["met"]
    pos = (met[["lon", "lat"]].dropna() if {"lon", "lat"} <= set(met)
           else pd.DataFrame())
    if t is None or not len(pos) or not pos.index[0] <= t <= pos.index[-1]:
        mark_src.data = dict(lon=[], lat=[])
        for sp in mark_spans:
            sp.visible = False
        mark_div.text = ("<i>Click a time series or the ship track to place "
                         "a marker; double-click to clear.</i>")
        return
    i = pos.index.get_indexer([t], method="nearest")[0]
    t = pos.index[i]
    lon, lat = pos["lon"].iloc[i], pos["lat"].iloc[i]
    mark_src.data = dict(lon=[lon], lat=[lat])
    for sp in mark_spans:
        sp.update(location=ts2ms(t), visible=True)
    g = met.loc[t].get
    vals = " &nbsp; ".join(
        f"{lab} {g(c):{fmt}}" for c, lab, fmt in
        [("sst", "SST", ".3f"), ("sal", "S", ".3f"), ("sog", "SOG", ".1f")]
        if c in met and np.isfinite(g(c)))
    mark_div.text = (f"<span style='color:{MARK_COLOR}'>&#9670;</span> "
                     f"<b>Marker</b> {t:%m/%d %H:%M:%S} UTC &nbsp; "
                     f"{ddm(lat, 'N', 'S')}, {ddm(lon180(lon), 'E', 'W')} "
                     f"&nbsp; {vals} &nbsp; <i>(double-click to clear)</i>")


def on_time_tap(event):
    STATE["mark_t"] = ms2ts(event.x)
    draw_mark()


def on_map_tap(event):
    """Put the marker at the time of the track point nearest the click, or
    with Calculate distance on, pick a distance end point."""
    if dist_btn.active:
        pts = STATE["dist"] if len(STATE["dist"]) < 2 else []
        STATE["dist"] = pts + [(event.x, event.y)]
        draw_dist()
        return
    met = STATE["met"]
    if not {"lon", "lat"} <= set(met):
        return
    pos = met[["lon", "lat"]].dropna()
    if not len(pos):
        return
    coslat = np.cos(np.radians(event.y))
    d2 = ((pos["lon"] - event.x) * coslat) ** 2 + (pos["lat"] - event.y) ** 2
    STATE["mark_t"] = d2.idxmin()
    draw_mark()


def clear_mark(event):
    STATE["mark_t"] = None
    draw_mark()


def great_circle(lon1, lat1, lon2, lat2):
    """Distance (km) and initial bearing (deg T) from point 1 to point 2."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    km = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    brg = np.degrees(np.arctan2(np.sin(dl) * np.cos(p2),
                                np.cos(p1) * np.sin(p2)
                                - np.sin(p1) * np.cos(p2) * np.cos(dl))) % 360
    return km, brg


def draw_dist():
    """Show the Calculate distance points, their positions and distance."""
    pts = STATE["dist"] if dist_btn.active else []
    dist_src.data = dict(lon=[p[0] for p in pts], lat=[p[1] for p in pts])
    if not dist_btn.active:
        dist_div.text = ""
        return
    rows = [f"<b>{k}</b> {lat:.5f}, {lon180(lon):.5f} "
            f"({ddm(lat, 'N', 'S')}, {ddm(lon180(lon), 'E', 'W')})"
            for k, (lon, lat) in zip("AB", pts)]
    if len(pts) == 2:
        km, brg = great_circle(*pts[0], *pts[1])
        rows.append(f"<b>Distance</b> {km:.2f} km ({km / 1.852:.2f} nm), "
                    f"bearing A to B {brg:.0f}° T")
    rows.append(["<i>Click the first point on the map.</i>",
                 "<i>Click the second point.</i>",
                 "<i>Click again to start a new measurement.</i>"][len(pts)])
    dist_div.text = (f"<div style='font-size:12px; color:{DIST_COLOR}'>"
                     + "<br>".join(rows) + "</div>")


def on_dist_toggle(attr, old, new):
    STATE["dist"] = []
    draw_dist()


def ddm(x, pos, neg):
    hemi = pos if x >= 0 else neg
    x = abs(x)
    return f"{int(x)}° {60 * (x % 1):06.3f}' {hemi}"


def adcp_image(ds, comp, zplot):
    """Regularly gridded (depth x time) image of u or v for bokeh.image."""
    z = readers.bin_depths(ds)
    keep = z <= zplot
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


def sst_box(ship):
    """SST download box: cfg.SST_BOX, or SST_HALF_DEG of latitude around
    ship (lon, lat) with the same distance in longitude."""
    if cfg.SST_BOX is not None or ship is None:
        return cfg.SST_BOX
    lon, lat = ship
    h = cfg.SST_HALF_DEG
    hx = min(h / max(np.cos(np.radians(lat)), 0.1), 179)
    return (max(lat - h, -89.9), min(lat + h, 89.9), lon - hx, lon + hx)


def ship_near_edge(lon, lat, ship):
    """True if ship (lon, lat) is outside the middle half of the grid."""
    if ship is None:
        return False
    x, y = wrap_lon(ship[0]), ship[1]
    lon = wrap_lon(lon)
    qx, qy = 0.25 * np.ptp(lon), 0.25 * np.ptp(lat)
    return not (lon.min() + qx <= x <= lon.max() - qx
                and lat.min() + qy <= y <= lat.max() - qy)


def refresh_sst(now, ship, warnings):
    """Show the satellite SST field, downloading a new one if the local
    copy is older than SST_REFRESH_H or (box following the ship) the ship
    is near its edge, at most every SST_RETRY_MIN after a failure. ship is
    (lon, lat) or None. Returns the analysis time, or None if no field."""
    f = cfg.SST_FILE
    age_h = ((now.timestamp() - f.stat().st_mtime) / 3600 if f.exists()
             else np.inf)
    stale = age_h > cfg.SST_REFRESH_H
    if not stale and cfg.SST_BOX is None:
        lon, lat, _, _ = readers.read_sst(f)
        stale = ship_near_edge(lon, lat, ship)
    box = sst_box(ship)
    last_try = STATE.get("sst_try")
    if stale and box is not None and (
            last_try is None
            or now - last_try > pd.Timedelta(minutes=cfg.SST_RETRY_MIN)):
        STATE["sst_try"] = now
        try:
            readers.download_sst(cfg.SST_ERDDAP, box, f)
        except Exception as e:
            warnings.append(f"SST download: {e}"
                            + (" (showing older copy)" if f.exists() else ""))
    if not f.exists():
        return None
    lon, lat, sst, t_sst = readers.read_sst(f)
    lon = wrap_lon(lon)
    order = np.argsort(lon)
    lon, sst = lon[order], sst[:, order]
    dlon, dlat = np.median(np.diff(lon)), np.median(np.diff(lat))
    sst_src.data = dict(image=[sst], x=[lon[0] - dlon / 2],
                        y=[lat[0] - dlat / 2], dw=[dlon * lon.size],
                        dh=[dlat * lat.size])
    sst_mapper.update(**dict(zip(("low", "high"), robust_limits(sst, 1, 99))))
    return t_sst


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
        t_met_first, t_met_last = readers.met_time_span(cfg.MET_DIR,
                                                        **MET_KW)
    except Exception as e:
        warnings.append(f"MET: {e}")
    ds_all = None
    sonar = cfg.SONARS[sonar_btn.active] if cfg.SONARS else None
    if sonar is not None:
        try:
            ds_all = readers.read_adcp(cfg.ADCP_FILES[sonar])
            ds_all = ds_all.assign(lon=ds_all["lon"].copy(
                data=wrap_lon(ds_all["lon"])))
            t_adcp_first = pd.Timestamp(ds_all["time"].values[0])
            t_adcp_last = pd.Timestamp(ds_all["time"].values[-1])
        except Exception as e:
            ds_all = None
            warnings.append(f"ADCP {sonar}: {e}")

    # --- depth axis of the u/v sections and depth slider follow the sonar
    zplot = zrange.end
    if ds_all is not None:
        zplot = cfg.ADCP_ZMAX_PLOT or float(
            np.ceil(np.nanmax(readers.bin_depths(ds_all)) / 50) * 50)
    zrange.end = zplot
    if zmax > zplot:  # e.g. back from os75 to os150 with a deep range
        zmin, zmax = cfg.ADCP_ZRANGE
        zrange.value = (zmin, zmax)
    for comp, f in uv_figs.items():
        f.y_range.start = zplot
        f.title.text = (f"ADCP {sonar or ''} {comp} (m/s, "
                        f"{'east' if comp == 'u' else 'north'})")

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
        met = read_met(t0, t1)
    except Exception as e:
        warnings.append(f"MET: {e}")
    STATE["met"] = met
    if len(met):
        line_src.data = dict(lon=met["lon"].values, lat=met["lat"].values)
        ts_src.data = {"t": met.index.values,
                       **{c: met[c].values if c in met else
                          np.full(len(met), np.nan)
                          for c in ("sst", "tsg_t", "sal", "wind", "wind_dir", "sog")}}
        pos = met[["lon", "lat"]].dropna()
        ends = pos.iloc[[0, -1]] if len(pos) else pos
        ends_src.data = dict(lon=ends["lon"].values, lat=ends["lat"].values,
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
            pos = read_met(t_met_last - pd.Timedelta(minutes=10),
                           t_met_last)[["lon", "lat"]].dropna()
            if len(pos):
                now_src.data = dict(lon=pos["lon"].values[-1:],
                                    lat=pos["lat"].values[-1:],
                                    t=pos.index.values[-1:],
                                    label=["Current position"])
        except Exception as e:
            warnings.append(f"MET: {e}")
    time_range.update(start=t0, end=t1)

    # --- basemap
    show_sst = BASEMAPS[basemap.active] == "Satellite SST"
    t_sst = None
    if show_sst:
        try:
            ship = (tuple(np.r_[now_src.data["lon"], now_src.data["lat"]])
                    if len(now_src.data["lon"]) else None)
            t_sst = refresh_sst(now, ship, warnings)
        except Exception as e:
            warnings.append(f"SST: {e}")
    bathy_r.visible = not show_sst
    sst_r.visible = show_sst

    # --- tracked items (moorings, drifters, ...)
    tracked = []  # (item, all its fixes) for the readout
    for item, src in zip(cfg.TRACKED, tracked_srcs):
        fixes = pd.DataFrame()
        try:
            fixes = readers.read_positions(item["dir"], t0=item.get("start"))
        except Exception as e:
            warnings.append(f"{item['name']}: {e}")
        tracked.append((item, fixes))
        win = fixes.loc[t0:t1] if len(fixes) else fixes
        src.data = dict(lon=wrap_lon(win["lon"]) if len(win) else [],
                        lat=win["lat"].values if len(win) else [],
                        t=win.index.values if len(win) else [])

    # --- ADCP
    ds = None
    if ds_all is not None:
        ds = ds_all.sel(time=slice(t0, t1))
        if ds.sizes["time"] == 0:
            ds = None
    if ds is not None:
        ubar, vbar = readers.depth_avg(ds, zmin, zmax)
        imgs = {c: adcp_image(ds, c, zplot) for c in ("u", "v")}
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
        tr = dict(lon=met["lon"].values, lat=met["lat"].values,
                  t=met.index.values, val=met[col].values)
    elif src_kind == "adcp" and ds is not None:
        tr = dict(lon=ds["lon"].values, lat=ds["lat"].values,
                  t=ds["time"].values, val=np.hypot(ubar, vbar))
    else:
        tr = dict(lon=[], lat=[], t=[], val=[])
    track_src.data = tr
    lo, hi = robust_limits(tr["val"])
    track_cbar.title = (f"{color_select.value} ({units})"
                        if src_kind == "met" else
                        f"ADCP speed {zmin:.0f}-{zmax:.0f} m ({units})")
    # Track colored by a water temperature on the SST background: put both
    # on one color scale (and colorbar) so ship and satellite SST can be
    # compared directly. Otherwise the title gives the satellite SST range.
    title = "Ship track"
    if show_sst:
        title += (f" on {cfg.SST_NAME}, {t_sst:%Y-%m-%d}"
                  if t_sst is not None else f" ({cfg.SST_NAME} not available)")
    if show_sst and t_sst is not None:
        if col in ("sst", "tsg_t"):
            lo, hi = min(lo, sst_mapper.low), max(hi, sst_mapper.high)
            sst_mapper.update(low=lo, high=hi)
            track_cbar.title = ("Underway and satellite SST (°C)"
                                if col == "sst" else
                                "TSG temperature and satellite SST (°C)")
        else:
            title += (f" ({sst_mapper.low:.1f}-{sst_mapper.high:.1f} °C)")
    map_fig.title.text = title
    track_mapper.update(palette=palette, low=lo, high=hi)

    # --- map extent: fit to the track once (or on Zoom to track), then
    # keep the user's zoom unless Auto-zoom is on
    if autozoom.active or not STATE["zoomed"]:
        STATE["zoomed"] = set_map_ranges(
            np.concatenate([line_src.data["lon"], tr["lon"],
                            *[s.data["lon"] for s in tracked_srcs]]),
            np.concatenate([line_src.data["lat"], tr["lat"],
                            *[s.data["lat"] for s in tracked_srcs]]))
    draw_mark()

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
        coslat = np.cos(np.radians(df["lat"].mean() if len(df) else 0))
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
        def g(k):
            return last.get(k, np.nan)
        rows += [
            ("Time (UTC)", f"{met.index[-1]:%Y-%m-%d %H:%M:%S}"),
            ("Position", f"{g('lat'):.5f}, {lon180(g('lon')):.5f} &nbsp; "
                         f"({ddm(g('lat'), 'N', 'S')}, "
                         f"{ddm(lon180(g('lon')), 'E', 'W')})"),
            ("SOG / COG", f"{g('sog'):.1f} kn / {g('cog'):.0f}°"),
            ("SST / TSG T / S", f"{g('sst'):.3f} °C / {g('tsg_t'):.3f} °C / "
                                f"{g('sal'):.3f} psu"),
            ("True wind", f"{g('wind'):.1f} m/s ({g('wind') / KN2MS:.0f} kn) "
                          f"from {g('wind_dir'):.0f}°"),
        ]
        if np.isfinite(g("depth")):
            rows.append(("Water depth (multibeam)", f"{g('depth'):.0f} m"))
    if ds is not None and np.isfinite(ubar).any():
        i = np.where(np.isfinite(ubar))[-1][-1]
        spd = np.hypot(ubar[i], vbar[i])
        toward = np.degrees(np.arctan2(ubar[i], vbar[i])) % 360
        rows.append((f"Current {zmin:.0f}-{zmax:.0f} m",
                     f"{spd:.2f} m/s ({spd / KN2MS:.1f} kn) toward "
                     f"{toward:.0f}° &nbsp; [u={ubar[i]:+.2f}, "
                     f"v={vbar[i]:+.2f}]"))
    for item, fixes in tracked:
        if len(fixes):
            b = fixes.iloc[-1]
            lon = lon180(b["lon"])
            rows.append((f"{item['name']} (latest)",
                         f"{b['lat']:.5f}, {lon:.5f} &nbsp; "
                         f"({ddm(b['lat'], 'N', 'S')}, {ddm(lon, 'E', 'W')})"))
    ages = [age_html("MET", t_met_last, now, cfg.MET_STALE_MIN),
            age_html(f"ADCP {sonar or ''}", t_adcp_last, now, cfg.ADCP_STALE_MIN)]
    ages += [age_html(item["name"], fixes.index[-1] if len(fixes) else None,
                      now, item.get("stale_min", cfg.TRACKED_STALE_MIN))
             for item, fixes in tracked]
    table = "".join(f"<tr><td style='padding-right:12px'><b>{k}</b></td>"
                    f"<td>{v}</td></tr>" for k, v in rows)
    readout.text = (f"<table style='font-size:13px'>{table}</table>"
                    f"<div style='font-size:12px'>"
                    + " &nbsp;|&nbsp; ".join(ages) + "</div>")

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


def on_zoom():
    STATE["zoomed"] = False
    update()


zoom_btn.on_click(on_zoom)
dist_btn.on_change("active", on_dist_toggle)
map_fig.on_event(Tap, on_map_tap)
for f in time_figs:
    f.on_event(Tap, on_time_tap)
for f in [map_fig, *time_figs]:
    f.on_event(DoubleTap, clear_mark)
for (side, sign), btn in nudge_btns.items():
    btn.on_click(on_nudge(side, sign))
start_slide.on_change("value", on_drag("start"))
end_slide.on_change("value", on_drag("end"))
start_slide.on_change("value_throttled", on_change)
end_slide.on_change("value_throttled", on_end_release)
for w, attr in [(color_select, "value"),
                (zrange, "value_throttled"), (vec_mode, "active"),
                (follow, "active"), (autozoom, "active"),
                (basemap, "active"), (sonar_btn, "active")]:
    w.on_change(attr, on_change)

controls = column(
    status,
    row(start_slide, nudge_group("start")),
    row(end_slide, nudge_group("end")),
    zrange,
    row(window_div, follow, autozoom, zoom_btn),
    row(color_select, column(Div(text="&nbsp;", height=12), vec_mode)),
    row(Div(text="Map background:", styles={"margin-top": "6px"}), basemap,
        *([Div(text="ADCP:", styles={"margin-top": "6px"}), sonar_btn]
          if cfg.SONARS else [])),
    mark_div,
    row(dist_btn, dist_div),
)
layout = row(column(controls, map_fig),
             column(readout, uv_figs["u"], uv_figs["v"], *ts_figs))

update()
doc = curdoc()
doc.title = f"{cfg.CRUISE} situational awareness"
doc.add_root(layout)
doc.add_periodic_callback(update, cfg.REFRESH_S * 1000)
