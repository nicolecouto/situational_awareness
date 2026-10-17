"""Settings for the at-sea situational awareness dashboard.

For a new cruise, edit the CRUISE SETUP block below (names and paths). The
rest are display defaults that rarely need changing. See README.md for
what each data source is and where it lives.
"""
from pathlib import Path

HERE = Path(__file__).parent

# ============================================================ CRUISE SETUP
CRUISE = "RR2609"

# Cruise share (smb://rr-sci-filesvr.ucsd.edu/cruise/RR2609), mounted by Finder
CRUISE_DIR = Path("/Volumes/cruise") / CRUISE

# Underway (nav, met, TSG) data: one file per UTC day in MET_DIR, named by
# the strftime pattern MET_FILE. Only the MetAcq "Met Data - Corrected"
# format (SIO ships) has a reader so far, see readers.read_met.
MET_DIR = CRUISE_DIR / "metacq" / "data"
MET_FILE = "%y%m%d.MET"
MET_MISSING = -99
# Dashboard name: column name in the underway files. Drop a line if the
# ship doesn't have it. Units expected: lat/lon decimal deg, sog knots,
# cog and wind_dir deg (wind coming from), wind m/s, temperatures °C.
MET_COLUMNS = {
    "lat": "LA", "lon": "LO",            # Seapath GPS
    "cog": "CR", "sog": "SP",
    "sst": "ST",                         # SBE38, bow thruster intake
    "tsg_t": "TT", "sal": "SA",          # SBE45 TSG
    "fluor": "FL",
    "wind": "TW", "wind_dir": "TI",      # true wind speed, direction
    "depth": "MB",                       # multibeam depth
}

# UHDAS ADCP contour file. os75 is out of commission on RR2609, so default
# to os150nb. Set ADCP_FILE = None if there is no ADCP.
SONAR = "os150nb"
ADCP_FILE = (CRUISE_DIR / "adcp_uhdas" / CRUISE / "proc" / SONAR / "contour"
             / f"{SONAR}.nc")

# Bathymetry for the basemap: netCDF with 1-D lon/lat (or x/y) and depth
# or elevation (z, elevation, ...; negative is ocean). Subset it to the
# cruise area first. Set to None for no bathymetry.
BATHY_FILE = Path("/Users/ncouto/Dropbox/SIO/projects/muri/Planning/bathy/muri_bathy.nc")

# Use 0-360 longitudes everywhere. Set True if the cruise crosses 180°
# (e.g. the Ross Sea), so the track doesn't jump across the map.
LON_0_360 = False

# Satellite SST basemap area (lat min, lat max, lon min, lon max). None
# follows the ship: a box SST_HALF_DEG of latitude either side of it, re-
# downloaded when the ship gets near the edge.
SST_BOX = None
SST_HALF_DEG = 1.5

# Drifting mooring beacon (Xeos 1374, EK80 mooring). Every *.csv in this
# folder is read and merged, so new CSVs from Slack can just be dropped in.
# An empty or missing folder just shows no beacon.
BEACON_DIR = HERE / "beacon"
BEACON_NAME = "Xeos 1374"
BEACON_START = "2026-10-17 10:00"        # UTC; ignore older fixes (or None)
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
ADCP_ZMAX_PLOT = 400                     # max depth on u/v sections (m)
MAX_VECTORS = 60                         # subsample ADCP vectors on the map

# Flag data as stale (red in the readout) if older than this (min)
MET_STALE_MIN = 15
ADCP_STALE_MIN = 30
BEACON_STALE_MIN = 90
