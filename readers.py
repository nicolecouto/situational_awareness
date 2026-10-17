"""Readers for shipboard MetAcq, UHDAS ADCP, beacon, satellite SST, and
bathymetry files.

These are plain functions (no Bokeh) so they can be tested on their own.
Files on the cruise share are cached by modification time, so repeated
calls only re-read files that have changed.
"""
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

_cache = {}  # path -> (mtime, data)


def _cached(path, loader):
    """Return loader(path), re-reading only if the file mtime changed.

    If the read fails (e.g. the file is being rewritten), fall back to the
    last good copy when there is one.
    """
    path = Path(path)
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        data = loader(path)
    except Exception:
        if hit is not None:
            return hit[1]
        raise
    _cache[path] = (mtime, data)
    return data


# ---------------------------------------------------------------- MetAcq
# Defaults are for MetAcq on SIO ships; config.py passes the cruise's own.
MET_FILE = "%y%m%d.MET"
MET_MISSING = -99


def _read_met_file(path, pattern=MET_FILE, missing=MET_MISSING):
    """Parse one MetAcq daily file into a DataFrame indexed by UTC time."""
    cols = None
    with open(path) as f:
        for line in f:
            if line.startswith("#Time"):
                cols = line[1:].split()
                break
            if not line.startswith("#"):
                break
    if cols is None:
        raise ValueError(f"No #Time header line in {path}")

    df = pd.read_csv(path, sep=r"\s+", comment="#", names=cols, header=None,
                     dtype={"Time": str}, on_bad_lines="skip")
    df = df.replace(missing, np.nan)

    # Time: GPS epoch seconds (ZD) where valid, else file date + HHMMSS
    day = pd.to_datetime(path.name, format=pattern, utc=True)
    hms = df["Time"].str.zfill(6)
    t_file = day + pd.to_timedelta(hms.str[:2].astype(int), unit="h") \
                 + pd.to_timedelta(hms.str[2:4].astype(int), unit="m") \
                 + pd.to_timedelta(hms.str[4:6].astype(int), unit="s")
    if "ZD" in df:
        t_gps = pd.to_datetime(df["ZD"], unit="s", utc=True, errors="coerce")
        ok = (t_gps - t_file).abs() < pd.Timedelta(minutes=5)
        t = t_gps.where(ok, t_file)
    else:
        t = t_file
    df.index = pd.DatetimeIndex(t).tz_convert(None)  # naive UTC
    df.index.name = "time"
    return df.drop(columns="Time")


def _met_loader(pattern, missing, columns):
    """Loader for _cached that renames the file's columns to the dashboard
    names in columns ({name: file column}), keeping only those."""
    def load(path):
        df = _read_met_file(path, pattern, missing)
        if columns is None:
            return df
        keep = {c: name for name, c in columns.items() if c in df}
        return df[list(keep)].rename(columns=keep)
    return load


def met_files(met_dir, pattern=MET_FILE):
    """{day: path} for the daily files in met_dir whose names match the
    strftime pattern, sorted by day."""
    out = {}
    for f in Path(met_dir).iterdir():
        try:
            out[pd.to_datetime(f.name, format=pattern)] = f
        except ValueError:
            continue
    return dict(sorted(out.items()))


def read_met(met_dir, t0, t1, pattern=MET_FILE, missing=MET_MISSING,
             columns=None):
    """Underway data between t0 and t1 (naive UTC Timestamps) as a
    DataFrame. Only the daily files overlapping [t0, t1] are read. With
    columns ({name: file column}), columns are renamed to those names."""
    met_dir = Path(met_dir)
    days = pd.date_range(pd.Timestamp(t0).normalize(),
                         pd.Timestamp(t1).normalize(), freq="D")
    load = _met_loader(pattern, missing, columns)
    frames = []
    for d in days:
        path = met_dir / f"{d:{pattern}}"
        if path.exists():
            frames.append(_cached(path, load))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    return df.loc[t0:t1]


def met_time_span(met_dir, pattern=MET_FILE, missing=MET_MISSING,
                  columns=None):
    """(first, last) row times (naive UTC) over all daily files, or
    (None, None). Pass the same columns as to read_met, since files are
    cached by path."""
    files = list(met_files(met_dir, pattern).values())
    if not files:
        return None, None
    load = _met_loader(pattern, missing, columns)
    first = _cached(files[0], load)
    last = _cached(files[-1], load)
    return (first.index[0] if len(first) else None,
            last.index[-1] if len(last) else None)


# ---------------------------------------------------------------- beacon
def _read_beacon_file(path):
    """One CSV of beacon GPS fixes. Finds the time, lat and lon columns by
    name (e.g. "Timestamp"/"time_utc", "Lat"/"latitude", "Lon"/"longitude"),
    so the CSVs relayed on Slack and hand-made ones both work."""
    df = pd.read_csv(path, sep=None, engine="python", comment="#")
    cols = {c.lower().strip(): c for c in df.columns}

    def find(*keys):
        for k in keys:
            for lc, c in cols.items():
                if lc.startswith(k):
                    return c
        raise ValueError(f"{path.name}: no {keys[0]} column")

    out = pd.DataFrame({
        "lat": pd.to_numeric(df[find("lat")], errors="coerce").values,
        "lon": pd.to_numeric(df[find("lon")], errors="coerce").values,
    }, index=pd.to_datetime(df[find("time", "date")], utc=True,
                         format="mixed")
       .dt.tz_convert(None).values)
    return out.dropna()


def read_beacon(beacon_dir, t0=None, t1=None):
    """All beacon fixes from the *.csv files in beacon_dir, merged, sorted
    by time and with duplicate times dropped. Returns a DataFrame (lat, lon)
    indexed by naive UTC time, empty if there are no files."""
    files = sorted(Path(beacon_dir).glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=["lat", "lon"],
                            index=pd.DatetimeIndex([]))
    df = pd.concat([_cached(f, _read_beacon_file) for f in files])
    df = df[~df.index.round("s").duplicated(keep="last")].sort_index()
    if t0 is not None or t1 is not None:
        df = df.loc[t0:t1]
    return df


# ----------------------------------------------------------- satellite SST
def download_sst(erddap, box, out_path, timeout=20):
    """Download the latest day of analysed_sst inside box (lat0, lat1, lon0,
    lon1) from an ERDDAP griddap dataset (-180 to 180 longitudes) to
    out_path (netCDF). Longitudes may be -180..180 or 0..360, and a box
    that crosses 180° is fetched in two pieces and saved in 0..360. Writes
    to a temporary file first so a failed download never replaces a good
    copy."""
    lat0, lat1, lon0, lon1 = box
    lon0, lon1 = (lon0 + 180) % 360 - 180, (lon1 + 180) % 360 - 180
    spans = [(lon0, lon1)] if lon0 < lon1 else [(lon0, 180), (-180, lon1)]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pieces = []
    for i, (a, b) in enumerate(spans):
        url = (f"{erddap}.nc?analysed_sst%5B(last)%5D%5B({lat0}):({lat1})%5D"
               f"%5B({a}):({b})%5D")
        tmp = out_path.with_suffix(f".part{i}")
        with urllib.request.urlopen(url, timeout=timeout) as r:
            tmp.write_bytes(r.read())
        pieces.append(tmp)
    if len(pieces) == 1:
        pieces[0].replace(out_path)
        return
    dss = [xr.open_dataset(f).load() for f in pieces]
    ds = xr.concat([d.assign_coords(longitude=d["longitude"] % 360)
                    for d in dss], "longitude")
    tmp = out_path.with_suffix(".part")
    ds.to_netcdf(tmp)
    tmp.replace(out_path)
    for f in pieces:
        f.unlink()


def _read_sst_file(path):
    with xr.open_dataset(path) as ds:
        da = ds["analysed_sst"].isel(time=-1).sortby("latitude").load()
    return (da["longitude"].values, da["latitude"].values,
            da.values.astype(float), pd.Timestamp(da["time"].values))


def read_sst(path):
    """Latest SST field from a file written by download_sst.

    Returns lon, lat (1-D, ascending), sst (lat x lon, °C, NaN over land)
    and the time of the analysis.
    """
    return _cached(path, _read_sst_file)


# ------------------------------------------------------------------ ADCP
def _read_adcp_file(path):
    with xr.open_dataset(path) as ds:
        ds = ds[["u", "v", "pflag", "lon", "lat", "depth",
                 "uship", "vship", "heading"]].load()
    good = ds["pflag"] == 0
    ds["u"] = ds["u"].where(good)
    ds["v"] = ds["v"].where(good)
    return ds


def read_adcp(nc_path, t0=None, t1=None):
    """UHDAS contour netCDF (e.g. os150nb.nc) with flagged bins set to NaN.

    Returns an xarray Dataset with u, v (m/s, absolute), lon, lat, depth.
    """
    ds = _cached(nc_path, _read_adcp_file)
    if t0 is not None or t1 is not None:
        ds = ds.sel(time=slice(t0, t1))
    return ds


def bin_depths(ds):
    """1-D bin depths (m), from the median over time of depth[bin, time]."""
    d = ds["depth"]
    return np.nanmedian(d.values, axis=d.dims.index("time")) \
        if "time" in d.dims else d.values


def depth_avg(ds, zmin, zmax):
    """Mean u, v (m/s) over bins with zmin <= depth <= zmax, per ensemble."""
    z = bin_depths(ds)
    zdim = [dim for dim in ds["u"].dims if dim != "time"][0]
    sel = (z >= zmin) & (z <= zmax)
    if not sel.any():  # range narrower than a bin: use the nearest bin
        sel = np.zeros_like(z, dtype=bool)
        sel[np.nanargmin(np.abs(z - 0.5 * (zmin + zmax)))] = True
    u = ds["u"].isel({zdim: sel}).mean(zdim, skipna=True)
    v = ds["v"].isel({zdim: sel}).mean(zdim, skipna=True)
    return u.values, v.values


def surface_bin(ds):
    """u, v (m/s) from the shallowest bin with good data in each ensemble."""
    zdim = [dim for dim in ds["u"].dims if dim != "time"][0]
    u = ds["u"].transpose("time", zdim).values
    v = ds["v"].transpose("time", zdim).values
    first = np.argmax(np.isfinite(u), axis=1)
    rows = np.arange(u.shape[0])
    return u[rows, first], v[rows, first]


# ------------------------------------------------------------ Bathymetry
def _pick(ds, names, what):
    for n in names:
        if n in ds.variables:
            return n
    raise ValueError(f"No {what} variable (tried {', '.join(names)})")


def load_bathy(path, stride=1):
    """Bathymetry grid (lon, lat, z) decimated by stride, z negative in the
    ocean. Works with the usual names (lon/lat/z, x/y/z, GEBCO and IBCSO
    lon/lat/elevation)."""
    with xr.open_dataset(path) as ds:
        xn = _pick(ds, ["lon", "longitude", "x"], "longitude")
        yn = _pick(ds, ["lat", "latitude", "y"], "latitude")
        zn = _pick(ds, ["z", "elevation", "depth", "topo", "Band1"],
                   "elevation")
        da = ds[zn].isel({ds[xn].dims[0]: slice(None, None, stride),
                          ds[yn].dims[0]: slice(None, None, stride)}).load()
        lon, lat = ds[xn].values[::stride], ds[yn].values[::stride]
    return lon, lat, da.transpose(ds[yn].dims[0], ds[xn].dims[0]).values
