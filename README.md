# Situational awareness dashboard

2026 October 10, written for cruise RR2609 (R/V Roger Revelle).

A live browser dashboard to keep open during the cruise. It shows:

- **Map:** the ship track over bathymetry or satellite SST ("Map background" buttons) for the chosen time window, colored by SST, TSG temperature, salinity, fluorescence, ship speed over ground (SOG) or through the water (STW), or ADCP current speed. Optional ADCP current vectors (shallowest good bin, or averaged over a chosen depth range). A blue circle marks the start of the window shown, a red circle the end of the window, and a red x the ship's current position (latest MET fix, even when the window is in the past). A legend in the corner labels them. Small dots are position fixes in the same time window from anything listed in `TRACKED` (on RR2609, red for the drifting EK80 mooring beacon, Xeos 1374). The readout gives each item's latest position and how old that fix is (red if stale).
- **ADCP sections:** u (east) and v (north) vs time and depth over the same window. A dashed box shows the depth range used for averaging.
- **Underway time series:** SST and TSG temperature, salinity, true wind speed, speed over ground (SOG) and forward speed through water (STW) on separate panels, each with the raw 15 s data and a running mean (`SPEED_SMOOTH_S`, 120 s).
- **Readout:** latest time, position (decimal and degrees-minutes), SOG/COG, STW (forward and starboard), SST/TSG/salinity, true wind, multibeam depth, depth-averaged current, and how old the latest MET and ADCP data are (red if stale).

Everything re-reads every 60 s. Files are cached by modification time, so a refresh only re-reads the files that changed.

## Running it

One-time setup (on any computer):

```
git clone https://github.com/nicolecouto/situational_awareness.git
cd situational_awareness
conda env create -f environment.yml
```

Mount the cruise share in Finder (Go > Connect to Server, `smb://rr-sci-filesvr.ucsd.edu/cruise`). It should then appear at `/Volumes/cruise/RR2609`. If it is mounted somewhere else (e.g. `/Volumes/cruise-1` when a second share of the same name is already mounted, or `/mnt/cruise` on Linux), point the dashboard at it with an environment variable rather than editing `config.py`:

```
export CRUISE_SHARE=/Volumes/cruise-1
```

The bathymetry file is in the repo (`bathy/muri_bathy.nc`), so nothing else needs to be copied over. To get the latest code later, run `git pull` in the repo folder.

Start the dashboard from the folder that contains the repo:

```
conda activate muri-sea
cd ..
bokeh serve --show situational_awareness
```

This opens http://localhost:5006/situational_awareness. Leave the terminal running. To let others on the ship network view it, add `--allow-websocket-origin=<your-ip>:5006` and share `http://<your-ip>:5006/situational_awareness`.

### Controls

- **Start / End:** sliders that set the time window shown everywhere (map, ADCP sections, time series). They span all available data, and the chosen times are printed in the slider titles, with the window length below. The window is limited to 15 min - 72 h (`WINDOW_MIN_H`, `WINDOW_MAX_H` in `config.py`). If you drag one slider past those limits, the other one moves with it. The down/up arrow buttons next to each slider move that end of the window by 15 min (`NUDGE_MIN`).
- **Color track by:** the variable used to color the track.
- **ADCP depth range:** the depth range used for the depth-averaged vectors, the "ADCP speed" track color, and the readout current.
- **Vectors:** none, shallowest good bin, or depth range average.
- **Follow latest:** keeps End at the newest data, with the same window length, as new data arrive. Moving End back in time (slider or button) turns this off, and moving it all the way to the latest data turns it back on.
- **Map zoom:** the map fits the track when the page opens, then keeps whatever pan and zoom you set. **Zoom to track** refits it once. **Auto-zoom map** (off by default) refits it at every refresh.
- **Calculate distance:** turn the button on, then click two points on the map (A and B) to see their positions, the great-circle distance in km and nautical miles, and the bearing from A to B. A third click starts a new measurement, and turning the button off clears it. While it's on, map clicks pick points instead of placing the marker.
- **Marker:** click a time series or ADCP section to put an orange dashed line at that time on all of them, and an orange diamond on the map at the ship's position then. Or click the ship track on the map to mark the nearest fix. The marker snaps to the nearest underway fix, the line under the controls gives its time, position, SST, salinity, SOG and STW, and double-clicking any plot clears it. It stays through refreshes while its time is in the window.
- **ADCP:** which sonar (os150nb or os75nb) to use for the u/v sections, vectors, "ADCP speed" track color and readout current. The section depth axis and the depth range slider extend to the deepest bin of that sonar (about 414 m for os150nb, 974 m for os75nb). If the depth range is deeper than the sonar you switch to, it goes back to the default (`ADCP_ZRANGE`).
- **Map background:** bathymetry, or the latest daily satellite SST field (see Data sources). When the track is colored by SST or TSG temperature, it shares the satellite SST color scale and the colorbar on the right, so ship and satellite SST can be compared directly. Otherwise the map title gives the satellite SST range.

## Setting up a new cruise

Everything that changes from cruise to cruise is in the **CRUISE SETUP** block at the top of `config.py`:

1. `CRUISE` and `CRUISE_DIR` (where the ship's share is mounted).
2. Underway data: `MET_DIR`, the daily file name pattern `MET_FILE` (strftime codes, e.g. `%y%m%d.MET`), `MET_MISSING`, and `MET_COLUMNS`, which maps the dashboard's names (`lat`, `lon`, `sst`, `sog`, ...) to the column names in the ship's files. Leave out anything the ship doesn't log.
3. ADCP: `SONARS` (the first is the default) and `ADCP_FILES` (the UHDAS contour netCDF for each), or `SONARS = []`.
4. `BATHY_FILE`, a netCDF subset for the cruise area (or `None`). The usual variable names work (lon/lat/z, x/y/z, GEBCO and IBCSO lon/lat/elevation).
5. `LON_0_360 = True` if the cruise crosses 180° (e.g. Ross Sea), so all longitudes are drawn as 0-360.
6. `SST_BOX`: leave as `None` to follow the ship, or give a fixed box.
7. `TRACKED`: one entry per thing to follow on the map (drifting mooring, drifters, gliders, small boat), or `TRACKED = []`.
8. `STATIONS`: fixed positions to mark on the map (deployed moorings, planned stations), each with a name, lat, lon, and optional marker shape and color, or `STATIONS = []`. The **Show moorings** checkbox (next to Zoom to track) hides or shows them all.

**Caveat:** the only underway reader so far is for the MetAcq format used on SIO ships (`readers._read_met_file`). It needs a `#Time` header line with whitespace-separated columns and an `HHMMSS` `Time` column. On a ship that logs in another format, add a reader that returns a DataFrame indexed by UTC time, then use it in `readers._met_loader`. The rest of the dashboard only sees the names in `MET_COLUMNS`.

## Files

- `main.py` - Bokeh server app (layout, widgets, update loop)
- `readers.py` - readers for MetAcq, UHDAS ADCP, tracked-position CSVs, satellite SST and bathymetry (no Bokeh, can be used on their own)
- `config.py` - paths, sonar, refresh rate, defaults. **Edit the CRUISE SETUP block for a new cruise.**
- `environment.yml` - conda environment `muri-sea`
- `bathy/muri_bathy.nc` - bathymetry basemap for the cruise area

## Data sources

All shipboard data come from the cruise share **`smb://rr-sci-filesvr.ucsd.edu/cruise/RR2609`**, mounted locally at **`/Volumes/cruise/RR2609`** (or `$CRUISE_SHARE/RR2609` if `CRUISE_SHARE` is set). The share's overview of the instruments is in `/Volumes/cruise/RR2609/RR2609_README.txt`.

### MetAcq underway system (navigation, met, TSG, SST)

- **Data:** `/Volumes/cruise/RR2609/metacq/data/YYMMDD.MET` (one file per UTC day, e.g. `/Volumes/cruise/RR2609/metacq/data/261011.MET`)
- **Acquisition config** (sensors and modules): `/Volumes/cruise/RR2609/metacq/settings/RR26G.acq`
- **Manual** (parameter definitions are in Appendix A): `/Volumes/cruise/RR2609/metacq/docs/MetAcq.pdf`
- **Format:** "Met Data - Corrected". There are 3 `#` header lines, then a `#Time ...` line with column names, then whitespace-delimited rows every 15 s. Missing values are `-99`. The file on the share is updated about every 5 min.
- **Time:** taken from `ZD` (GPS time, seconds since 1970-01-01 UTC). If `ZD` is bad, it falls back to the file date plus `Time` (HHMMSS UTC).
- **Columns used:**

| Column | Meaning (MetAcq manual, Appendix A) | Units | Sensor on RR2609 |
|---|---|---|---|
| `LA`, `LO` | Latitude, longitude | decimal deg | Seapath GPS |
| `CR`, `SP` | Course and speed over ground | deg, knots | GPS |
| `SL`, `SX` | Speed log: speed through water, longitudinal (+ forward) and transverse (+ starboard) | knots | UHDAS OS150 speed log (NMEA VBW) |
| `ST` | Sea surface temperature | °C | SBE38, bow thruster UCW intake |
| `TT`, `SA` | TSG temperature, salinity | °C, psu | SBE45 |
| `FL` | Fluorometer | µg/l | |
| `TW`, `TI` | True wind speed, direction (coming from) | m/s, deg | |
| `MB` | Multibeam depth | m | |

### Shipboard ADCP (UHDAS)

- **Data:** `/Volumes/cruise/RR2609/adcp_uhdas/RR2609/proc/os150nb/contour/os150nb.nc` (default) and `/Volumes/cruise/RR2609/adcp_uhdas/RR2609/proc/os75nb/contour/os75nb.nc`, picked with the ADCP buttons. The cruise README said the os75 was out of commission, but its contour file has been updating since Oct 10.
- **UHDAS settings:** `/Volumes/cruise/RR2609/adcp_uhdas/settings/`
- **Processing status:** `/Volumes/cruise/RR2609/adcp_uhdas/RR2609/proc/<sonar>/cruise_info.txt` (one per sonar)
- **Format:** UHDAS/CODAS "contour" netCDF with 3-min ensembles: os150nb has 50 bins of 8 m from 22 to 414 m, os75nb 60 bins of 16 m from 30 to 974 m. It is updated by the shipboard processing about every 15-20 min.
- **Variables used:**
  - `time` (days since 2026-01-01)
  - `lon`, `lat`
  - `depth` (depth_cell x time)
  - `u`, `v` (absolute velocity, m/s)
  - `pflag` (bins with `pflag != 0` are set to NaN)
- **Caveat:** these are at-sea products. The calibration, heading correction and editing steps listed in `cruise_info.txt` have not been done, so treat velocities as preliminary.

### Tracked items (moorings, drifters, ...)

- **Setup:** `TRACKED` in `config.py`, a list with one entry per item: `name` and `dir` (a folder of CSVs), plus optional `start` (UTC; older fixes are ignored), `color`, `marker` (Bokeh marker name) and `stale_min`. Remove an entry when it's no longer needed, e.g. once a mooring is recovered.
- **Data:** every `*.csv` in each item's folder. The files are merged and duplicate fixes dropped, so updated CSVs can be dropped in as they arrive (any file name).
- **Format:** any delimiter, `#` comment lines allowed. It needs a time column (name starting with `time`, `Timestamp`, or `date`, UTC) and columns starting with `lat` and `lon` (decimal degrees, lon negative = W). Other columns are ignored.
- **RR2609:** the EK80 mooring broke free and its Xeos 1374 beacon reports by Iridium email, which Gunnar relays to Slack. Fixes are in `beacon/` and plotted from 2026-10-17 10:00 UTC, when it surfaced, so the Oct 12 deployment startup fixes are left out.
- `beacon/` is not tracked in git. Put other items' folders under `tracked/`, which is ignored too.

### Satellite SST

- **Data:** NOAA Geo-Polar Blended 5 km daily SST analysis (the field behind the OSPO contour charts), global, from CoastWatch ERDDAP dataset `noaacwBLENDEDsstDNDaily` (`SST_ERDDAP` in `config.py`).
- **Download:** only while the SST background is shown. It fetches the latest day in a box around the ship (`SST_HALF_DEG` of latitude either side) or in `SST_BOX`, saved to `sst/sst_latest.nc`. It downloads again when the copy is older than `SST_REFRESH_H` (3 h) or the ship nears the edge of the box. A box crossing 180° is fetched in two pieces. Each download is roughly 30 KB. If a download fails, the old copy stays up and the next try waits `SST_RETRY_MIN`.
- **Caveat:** the analysis lags real time by a day or two (the title gives its date). It is a 5 km blend, so it smooths fronts and filaments that single-pass imagery would show.
- `sst/` is not tracked in git.

### Bathymetry

- **Data:** `bathy/muri_bathy.nc` in this repo (`BATHY_FILE`, optional), copied from `muri/planning/bathy/muri_bathy.nc`
- **Contents:** variables `lat`, `lon`, `z` (m, negative is ocean), covering 34.8-37 N, 124-121 W. It is plotted at every 2nd point (`BATHY_STRIDE`). Isobaths are 100, 200, 500, 1000, 2000, 3000 and 4000 m, and the z = 0 contour serves as the coastline.
- **Caveat:** outside this box (e.g. north of 37 N toward San Francisco) the track is drawn on a blank background. Switch to the SST background there.
