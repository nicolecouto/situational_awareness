"""Readers for shipboard MetAcq, UHDAS ADCP, and bathymetry files.

These are plain functions (no Bokeh) so they can be tested on their own.
Files on the cruise share are cached by modification time, so repeated
calls only re-read files that have changed.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

MET_MISSING = -99
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
def _read_met_file(path):
    """Parse one MetAcq YYMMDD.MET file into a DataFrame indexed by UTC time."""
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
    df = df.replace(MET_MISSING, np.nan)

    # Time: GPS epoch seconds (ZD) where valid, else file date + HHMMSS
    day = pd.to_datetime(path.stem, format="%y%m%d", utc=True)
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


def read_met(met_dir, t0, t1):
    """MetAcq data between t0 and t1 (naive UTC Timestamps) as a DataFrame.

    Only the daily files overlapping [t0, t1] are read.
    """
    met_dir = Path(met_dir)
    days = pd.date_range(pd.Timestamp(t0).normalize(),
                         pd.Timestamp(t1).normalize(), freq="D")
    frames = []
    for d in days:
        path = met_dir / f"{d:%y%m%d}.MET"
        if path.exists():
            frames.append(_cached(path, _read_met_file))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    return df.loc[t0:t1]


def latest_met_time(met_dir):
    """Time of the last row in the newest MET file (naive UTC), or None."""
    files = sorted(Path(met_dir).glob("[0-9]" * 6 + ".MET"))
    if not files:
        return None
    df = _cached(files[-1], _read_met_file)
    return df.index[-1] if len(df) else None


def earliest_met_time(met_dir):
    """Time of the first row in the oldest MET file (naive UTC), or None."""
    files = sorted(Path(met_dir).glob("[0-9]" * 6 + ".MET"))
    if not files:
        return None
    df = _cached(files[0], _read_met_file)
    return df.index[0] if len(df) else None


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
def load_bathy(path, stride=1):
    """Bathymetry grid (lon, lat, z) decimated by stride."""
    with xr.open_dataset(path) as ds:
        ds = ds.isel(lon=slice(None, None, stride),
                     lat=slice(None, None, stride)).load()
    return ds["lon"].values, ds["lat"].values, ds["z"].values
