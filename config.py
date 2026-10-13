"""Settings for the at-sea situational awareness dashboard.

Edit these for a new cruise or sonar. See README.md for what each data
source is and where it lives.
"""
from pathlib import Path

CRUISE = "RR2609"

# Cruise share (smb://rr-sci-filesvr.ucsd.edu/cruise/RR2609), mounted by Finder
CRUISE_DIR = Path("/Volumes/cruise") / CRUISE

# MetAcq underway data: one YYMMDD.MET file per UTC day
MET_DIR = CRUISE_DIR / "metacq" / "data"

# UHDAS ADCP. os75 is out of commission on RR2609, so default to os150nb.
SONAR = "os150nb"
ADCP_FILE = (CRUISE_DIR / "adcp_uhdas" / CRUISE / "proc" / SONAR / "contour"
             / f"{SONAR}.nc")

# Bathymetry for the basemap (lat, lon, z; z < 0 is ocean)
BATHY_FILE = Path("/Users/ncouto/Dropbox/SIO/projects/muri/Planning/bathy/muri_bathy.nc")
BATHY_STRIDE = 2                         # decimate grid for plotting speed
ISOBATHS = [-100, -200, -500, -1000, -2000, -3000, -4000]

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
