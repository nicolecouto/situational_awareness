"""Settings for the at-sea situational awareness dashboard.

For a new cruise, edit the CRUISE SETUP block below (names and paths). The
rest are display defaults that rarely need changing. See README.md for
what each data source is and where it lives.
"""
import os
from pathlib import Path

HERE = Path(__file__).parent

# ============================================================ CRUISE SETUP
CRUISE = "RR2609"

# Cruise share (smb://rr-sci-filesvr.ucsd.edu/cruise/RR2609), mounted by Finder
# at /Volumes/cruise. If it's mounted elsewhere on this computer, set the
# CRUISE_SHARE environment variable to the mount point instead of editing this.
CRUISE_DIR = Path(os.environ.get("CRUISE_SHARE", "/Volumes/cruise")) / CRUISE

# Underway (nav, met, TSG) data: one file per UTC day in MET_DIR, named by
# the strftime pattern MET_FILE. Only the MetAcq "Met Data - Corrected"
# format (SIO ships) has a reader so far, see readers.read_met.
MET_DIR = CRUISE_DIR / "metacq" / "data"
MET_FILE = "%y%m%d.MET"
MET_MISSING = -99
# Dashboard name: column name in the underway files. Drop a line if the
# ship doesn't have it. Units expected: lat/lon decimal deg, sog knots,
# cog and wind_dir deg (wind coming from), wind m/s, temperatures °C,
# stw (forward) and stw_x (starboard) speed through water in knots.
MET_COLUMNS = {
    "lat": "LA", "lon": "LO",            # Seapath GPS
    "cog": "CR", "sog": "SP",
    "stw": "SL", "stw_x": "SX",          # UHDAS OS150 speed log
    "sst": "ST",                         # SBE38, bow thruster intake
    "tsg_t": "TT", "sal": "SA",          # SBE45 TSG
    "fluor": "FL",
    "wind": "TW", "wind_dir": "TI",      # true wind speed, direction
    "depth": "MB",                       # multibeam depth
}

# UHDAS ADCP contour files, one per sonar, picked with the "ADCP" buttons.
# The first is the default. Set SONARS = [] if there is no ADCP.
SONARS = ["os150nb", "os75nb"]
ADCP_FILES = {s: CRUISE_DIR / "adcp_uhdas" / CRUISE / "proc" / s / "contour"
              / f"{s}.nc" for s in SONARS}

# Bathymetry for the basemap: netCDF with 1-D lon/lat (or x/y) and depth
# or elevation (z, elevation, ...; negative is ocean). Subset it to the
# cruise area first, and keep it in bathy/ so it travels with the repo. Set
# to None for no bathymetry.
BATHY_FILE = HERE / "bathy" / "muri_bathy.nc"

# Use 0-360 longitudes everywhere. Set True if the cruise crosses 180°
# (e.g. the Ross Sea), so the track doesn't jump across the map.
LON_0_360 = False

# Satellite SST basemap area (lat min, lat max, lon min, lon max). None
# follows the ship: a box SST_HALF_DEG of latitude either side of it, re-
# downloaded when the ship gets near the edge.
SST_BOX = None
SST_HALF_DEG = 1.5

# Other things to track on the map (drifting moorings, drifters, gliders,
# small boats, ...): position fixes from every *.csv in "dir", merged, so new
# CSVs can just be dropped in. Each CSV needs time (UTC), lat and lon
# columns. Optional keys: "start" (UTC; ignore older fixes), "color",
# "marker" (a Bokeh marker name), "stale_min" (age shown red after this).
# Delete an entry when it's no longer needed, or set TRACKED = [].
# Example (RR2609, the EK80 mooring that broke free, recovered 2026-10-17):
#   {"name": "Xeos 1374 (EK80 mooring)", "dir": HERE / "beacon",
#    "start": "2026-10-17 10:00", "color": "red", "stale_min": 90},
TRACKED = []

# Fixed positions to mark on the map (deployed moorings, stations, ...).
# Keys: "name", "lat", "lon" (decimal deg), and optional "marker" (a Bokeh
# marker name, e.g. triangle, inverted_triangle, square, diamond, circle)
# and "color".
STATIONS = [
    {"name": "YT source", "lat": 35.66402, "lon": -122.00135,
     "marker": "inverted_triangle", "color": "green"},
    {"name": "YT VLA", "lat": 35.65855, "lon": -122.00246,
     "marker": "triangle", "color": "yellow"},
    {"name": "YT EK80", "lat": 35.65263, "lon": -122.00201,
     "marker": "square", "color": "hotpink"},
    # From planning/sarah/June25_M1200_N3320.ipynb
    {"name": "M1200", "lat": 35.6437, "lon": -122.0020,
     "marker": "circle", "color": "red"},
    {"name": "N3320", "lat": 36.1007, "lon": -122.5992,
     "marker": "circle", "color": "red"},
]
# ======================================================== end CRUISE SETUP

BATHY_STRIDE = 2                         # decimate grid for plotting speed
ISOBATHS = [-100, -200, -500, -1000, -2000, -3000, -4000]

# Satellite SST: NOAA Geo-Polar Blended 5 km daily analysis (the field
# behind the OSPO contour charts), global, from CoastWatch ERDDAP. The
# latest day is downloaded to SST_FILE when the layer is shown and the copy
# is older than SST_REFRESH_H.
SST_ERDDAP = ("https://coastwatch.noaa.gov/erddap/griddap/"
              "noaacwBLENDEDsstDNDaily")
SST_NAME = "Geo-Polar Blended 5 km SST"
SST_FILE = HERE / "sst" / "sst_latest.nc"
SST_REFRESH_H = 3
SST_RETRY_MIN = 10                       # wait this long after a failed download

# Refresh and display defaults
REFRESH_S = 60                           # how often to re-read data (s)
WINDOW_H = 1.0                           # default time window length (h)
WINDOW_MIN_H = 0.25                      # shortest allowed window (h)
WINDOW_MAX_H = 72.0                      # longest allowed window (h)
WINDOW_STEP_MIN = 5                      # start/end slider step (min)
NUDGE_MIN = 15                           # start/end button step (min)
ADCP_ZRANGE = (20, 50)                   # default depth-average range (m)
ADCP_ZMAX_PLOT = None                    # max depth on u/v sections (m), None = deepest bin
MAX_VECTORS = 60                         # subsample ADCP vectors on the map
SPEED_SMOOTH_S = 120                     # running mean on SOG/STW plots (s)

# Flag data as stale (red in the readout) if older than this (min)
MET_STALE_MIN = 15
ADCP_STALE_MIN = 30
TRACKED_STALE_MIN = 90                   # default for TRACKED entries
