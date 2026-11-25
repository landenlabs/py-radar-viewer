#!/usr/bin/env python3
# ----------------------------------------------------------------------
# Copyright (c) 2026 LanDen Labs - Dennis Lang
# https://landenlabs.com
# ----------------------------------------------------------------------
"""radar-viewer.py - Qt map viewer for single-site weather radar.

Networks:
  * MeteoSwiss (CH) - the five Swiss C-band radars. Single-elevation ODIM
    HDF5 sweeps from the EUMETNET MeteoGate "Open Radar Data" (ORD) 24-hour
    rolling S3 cache:
        <bucket>/YYYY/MM/DD/CH/<site>/SCAN/<site>@YYYYMMDDTHHMM@<elev>@DBZH_TH_VRADH.h5
  * NWS NEXRAD (USA) - the WSR-88D network. Full NEXRAD Level II volumes
    (Archive II, bzip2-compressed message 31 radials) from the public
    Unidata/NOAA AWS bucket:
        https://unidata-nexrad-level2.s3.amazonaws.com/YYYY/MM/DD/KTLX/KTLX20261125_140753_V06

Both are drawn as PPI sweeps on a slippy (Web Mercator) basemap.

Layout: menu bar, toolbar with a time line, a collapsible navigation panel
on the left (sites, products, elevations, scan times, display options) and
the map viewer filling the remaining width.

Usage:
    python3 radar-viewer.py [file.h5 | KTLX20261125_140753_V06]
"""

import bz2
import gzip
import io
import math
import os
import re
import struct
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import numpy as np

try:
    import h5py
except ImportError:  # pragma: no cover - reported in a dialog at startup
    h5py = None

from PyQt6.QtCore import (
    QByteArray, QObject, QPointF, QRectF, QSettings, QSize, QStandardPaths,
    Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QIcon, QImage, QImageReader,
    QKeySequence, QMovie, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtNetwork import (
    QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QSplitter, QStyle, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

APP_NAME = "Radar Viewer"
VERSION = "v6.11.20"
ORG_NAME = "LanDenLabs"
USER_AGENT = "qt-radar-viewer/%s (+https://landenlabs.com)" % VERSION.lstrip("v")

# ----------------------------------------------------------------------
# Radar sites
# ----------------------------------------------------------------------


@dataclass
class RadarSite:
    code: str          # ODIM node code (CH) or ICAO id (US); also the S3 folder
    name: str
    lat: float
    lon: float
    height: float      # m above sea level (CH: antenna, US: ground)
    wmo: str = ""
    network: str = "CH"
    region: str = ""   # US state / territory


NETWORK_NAMES = OrderedDict([("CH", "MeteoSwiss (CH)"), ("US", "NWS NEXRAD (USA)")])

# Coordinates as published in the ODIM /where group of each site's files.
CH_SITES = [
    RadarSite("chalb", "Albis", 47.28433, 8.51200, 938.0, "06661"),
    RadarSite("chdol", "La Dôle", 46.42511, 6.09941, 1682.0, "06699"),
    RadarSite("chlem", "Monte Lema", 46.04076, 8.83322, 1626.0, "06768"),
    RadarSite("chppm", "Plaine Morte", 46.37065, 7.48655, 2937.0, "06726"),
    RadarSite("chwei", "Weissfluhgipfel", 46.83497, 9.79446, 2850.0, "06776"),
]

# WSR-88D sites from api.weather.gov/radar/stations (state|id|name|lat|lon|elev m).
NEXRAD_TABLE = """
AK|PABC|Bethel|60.79194|-161.87637|49
AK|PACG|Sitka|56.85277|-135.52915|63
AK|PAEC|Nome|64.51139|-165.29498|18
AK|PAHG|Kenai|60.72591|-151.35144|74
AK|PAIH|Middleton Island|59.46194|-146.30109|20
AK|PAKC|King Salmon|58.67944|-156.62942|19
AK|PAPD|Fairbanks Faa|65.03511|-147.50140|790
AL|KBMX|Birmingham|33.17194|-86.76972|197
AL|KEOX|Ft Rucker|31.46055|-85.45938|144
AL|KHTX|Northeast Alabama|34.93055|-86.08361|537
AL|KMOB|Mobile|30.67944|-88.23972|63
AL|KMXX|Maxwell AFB|32.53664|-85.78975|136
AR|KLZK|Little Rock|34.83638|-92.26194|173
AR|KSRX|Western Arkansas|35.29041|-94.36188|200
AZ|KEMX|Tucson|31.89361|-110.63027|1586
AZ|KFSX|Flagstaff|34.57433|-111.19843|2261
AZ|KIWA|Phoenix|33.28916|-111.66999|415
AZ|KYUX|Yuma|32.49527|-114.65668|53
CA|KBBX|Beale AFB|39.49611|-121.63165|53
CA|KBHX|Eureka (Bunker Hill)|40.49833|-124.29215|732
CA|KDAX|Sacramento|38.50111|-121.67782|9
CA|KEYX|Edwards AFB|35.09777|-117.56074|846
CA|KHNX|San Joaquin Valley|36.31416|-119.63213|74
CA|KMUX|San Francisco|37.15522|-121.89843|1057
CA|KNKX|San Diego|32.91888|-117.04193|291
CA|KSOX|Santa Ana Mts|33.81773|-117.63599|927
CA|KVBX|Vandenberg AFB|34.83855|-120.39790|383
CA|KVTX|Los Angeles|34.41166|-119.17860|831
CO|KFTG|Denver|39.78663|-104.54580|1675
CO|KGJX|Grand Junction|39.06222|-108.21375|3059
CO|KPUX|Pueblo|38.45944|-104.18138|1615
DE|KDOX|Dover AFB|38.82555|-75.44000|15
FL|KAMX|Miami|25.61055|-80.41305|4
FL|KBYX|Key West|24.59694|-81.70333|2
FL|KEVX|Eglin Afb|30.56503|-85.92166|43
FL|KJAX|Jacksonville|30.48463|-81.70190|19
FL|KMLB|Melbourne|28.11305|-80.65444|11
FL|KTBW|Tampa|27.70527|-82.40194|12
FL|KTLH|Tallahassee|30.39749|-84.32889|19
GA|KFFC|Atlanta|33.36333|-84.56583|262
GA|KJGX|Robins AFB|32.67499|-83.35111|159
GA|KVAX|Moody AFB|30.89027|-83.00180|66
GU|PGUA|Andersen AFB|13.45583|144.81112|83
HI|PHKI|South Kauai|21.89389|-159.55249|69
HI|PHKM|Kamuela/Kohala Apt|20.12527|-155.77776|1174
HI|PHMO|Molokai|21.13277|-157.18026|415
HI|PHWA|South Shore|19.09500|-155.56887|421
IA|KDMX|Des Moines|41.73110|-93.72285|299
IA|KDVN|Quad Cities|41.61166|-90.58083|230
ID|KCBX|Boise|43.49021|-116.23602|942
ID|KSFX|Pocatello|43.10559|-112.68612|1364
IL|KILX|Lincoln|40.15049|-89.33679|188
IL|KLOT|Chicago|41.60444|-88.08444|202
IN|KIND|Indianapolis|39.70749|-86.28027|241
IN|KIWX|Northern Indiana|41.35860|-85.70000|292
IN|KVWX|Evansville,IN|38.26024|-87.72452|156
JP|RODN|Kadena AB|26.30194|127.90972|91
KR|RKJK|Kunsan AB|35.92417|126.62222|24
KR|RKSG|Camp Humphreys|37.20757|127.28556|439
KS|KDDC|Dodge City|37.76083|-99.96888|789
KS|KGLD|Goodland|39.36694|-101.70027|1113
KS|KICT|Wichita|37.65444|-97.44305|407
KS|KTWX|Topeka|38.99694|-96.23249|417
KY|KHPX|Ft Campbell|36.73666|-87.28499|172
KY|KJKL|Jackson,KY|37.59083|-83.31305|416
KY|KLVX|Louisville|37.97527|-85.94388|219
KY|KPAH|Paducah|37.06833|-88.77194|119
LA|KHDC|Hammond|30.51960|-90.40740|43
LA|KLCH|Lake Charles|30.12530|-93.21588|17
LA|KPOE|Ft Polk|31.15527|-92.97583|124
LA|KSHV|Shreveport|32.45083|-93.84124|83
MA|KBOX|Boston|41.95577|-71.13686|36
ME|KCBW|Caribou|46.03916|-67.80642|227
ME|KGYX|Portland,ME|43.89130|-70.25636|125
MI|KAPX|NCL Michigan|44.90634|-84.71953|446
MI|KDTX|Detroit|42.69999|-83.47166|336
MI|KGRR|Grand Rapids|42.89388|-85.54488|237
MI|KMQT|Marquette|46.53110|-87.54833|430
MN|KDLH|Duluth|46.83694|-92.20971|435
MN|KMPX|Minneapolis|44.84888|-93.56552|301
MO|KEAX|Pleasant Hill|38.81024|-94.26446|303
MO|KLSX|St Louis|38.69888|-90.68277|185
MO|KSGF|Springfield|37.23527|-93.40027|390
MS|KDGX|Jackson/Brandon,MS|32.27999|-89.98444|151
MS|KGWX|Columbus AFB|33.89691|-88.32919|155
MT|KBLX|Billings|45.85377|-108.60679|1109
MT|KGGW|Glasgow|48.20635|-106.62468|702
MT|KMSX|Missoula|47.04110|-113.98610|2417
MT|KTFX|Great Falls|47.45972|-111.38527|1140
NC|KLTX|Wilmington|33.98916|-78.42916|20
NC|KMHX|Morehead City|34.77583|-76.87639|9
NC|KRAX|Raleigh/Durham|35.66527|-78.49000|106
ND|KBIS|Bismarck|46.77083|-100.76027|505
ND|KMBX|Minot AFB|48.39249|-100.86443|455
ND|KMVX|Fargo/Grand Forks|47.52805|-97.32499|301
NE|KLNX|North Platte|41.95794|-100.57621|919
NE|KOAX|Omaha|41.32027|-96.36680|350
NE|KUEX|Grand Island|40.32083|-98.44194|602
NJ|KDIX|Philadelphia|39.94694|-74.41072|45
NM|KABX|Albuquerque|35.14972|-106.82388|1789
NM|KEPZ|El Paso|31.87305|-106.69799|1251
NM|KFDX|Cannon AFB|34.63416|-103.61888|1417
NM|KHDX|Holloman AFB|33.07699|-106.12002|1287
NV|KESX|Las Vegas|35.70111|-114.89138|1483
NV|KLRX|Elko|40.73972|-116.80277|2067
NV|KRGX|Reno|39.75405|-119.46202|2530
NY|KBGM|Binghamton|42.19969|-75.98472|490
NY|KBUF|Buffalo|42.94860|-78.73694|211
NY|KENX|Albany|42.58655|-74.06408|565
NY|KOKX|Brookhaven|40.86552|-72.86392|26
NY|KTYX|Ft Drum|43.75582|-75.68000|563
OH|KCLE|Cleveland|41.41305|-81.86000|233
OH|KILN|Cincinnati|39.42027|-83.82166|322
OK|KFDR|Altus AFB|34.36219|-98.97666|386
OK|KINX|Tulsa|36.17499|-95.56413|204
OK|KTLX|Norman|35.33305|-97.27775|370
OK|KVNX|Vance AFB|36.74083|-98.12749|369
OR|KMAX|Medford|42.08111|-122.71735|2290
OR|KPDT|Pendleton|45.69055|-118.85290|462
OR|KRTX|Portland,OR|45.71499|-122.96499|492
PA|KCCX|State College|40.92305|-78.00389|733
PA|KPBZ|Pittsburgh|40.53166|-80.21794|361
PR|TJUA|San Juan|18.11566|-66.07817|867
SC|KCAE|Columbia|33.94860|-81.11861|70
SC|KCLX|Charleston,SC|32.65552|-81.04219|35
SC|KGSP|Greer|34.88330|-82.21983|291
SD|KABR|Aberdeen|45.45583|-98.41305|397
SD|KFSD|Sioux Falls|43.58777|-96.72888|436
SD|KUDX|Rapid City|44.12471|-102.82999|939
TN|KMRX|Knoxville|36.16833|-83.40194|408
TN|KNQA|Memphis|35.34472|-89.87333|103
TN|KOHX|Nashville|36.24722|-86.56250|176
TX|KAMA|Amarillo|35.23333|-101.70927|1104
TX|KBRO|Brownsville|25.91555|-97.41860|7
TX|KCRP|Corpus Christi|27.78388|-97.51083|14
TX|KDFX|Laughlin AFB|29.27250|-100.28027|345
TX|KDYX|Dyess AFB|32.53833|-99.25416|462
TX|KEWX|Austin/San Antonio|29.70405|-98.02860|204
TX|KFWS|Dallas/Ft Worth|32.57277|-97.30313|212
TX|KGRK|Ft Hood|30.72166|-97.38277|164
TX|KHGX|Houston|29.47194|-95.07888|5
TX|KLBB|Lubbock|33.65413|-101.81416|1005
TX|KMAF|Midland/Odessa|31.94346|-102.18924|883
TX|KSJT|San Angelo|31.37111|-100.49221|576
UT|KICX|Cedar City|37.59104|-112.86221|3244
UT|KMTX|Salt Lake City|41.26277|-112.44777|1975
VA|KAKQ|Norfolk|36.98388|-77.00750|48
VA|KFCX|Roanoke|37.02416|-80.27416|874
VA|KLWX|Sterling|38.97610|-77.48750|89
VT|KCXX|Burlington|44.51110|-73.16639|97
WA|KATX|Seattle|48.19461|-122.49568|161
WA|KLGX|Langley Hill|47.11689|-124.10663|77
WA|KOTX|Spokane|47.68055|-117.62582|727
WI|KARX|La Crosse|43.82277|-91.19110|389
WI|KGRB|Green Bay|44.49862|-88.11110|216
WI|KMKX|Milwaukee|42.96777|-88.55055|292
WV|KRLX|Charleston,WV|38.31110|-81.72277|335
WY|KCYS|Cheyenne|41.15194|-104.80610|1868
WY|KRIW|Riverton/Lander|43.06610|-108.47729|1697
"""

US_REGIONS = {
    "AK": "Alaska", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona",
    "CA": "California", "CO": "Colorado", "DE": "Delaware", "FL": "Florida",
    "GA": "Georgia", "GU": "Guam", "HI": "Hawaii", "IA": "Iowa", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "JP": "Japan (Okinawa)", "KR": "South Korea",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "MA": "Massachusetts",
    "ME": "Maine", "MI": "Michigan", "MN": "Minnesota", "MO": "Missouri",
    "MS": "Mississippi", "MT": "Montana", "NC": "North Carolina",
    "ND": "North Dakota", "NE": "Nebraska", "NJ": "New Jersey",
    "NM": "New Mexico", "NV": "Nevada", "NY": "New York", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VA": "Virginia", "VT": "Vermont",
    "WA": "Washington", "WI": "Wisconsin", "WV": "West Virginia", "WY": "Wyoming",
}


def _nexrad_sites():
    out = []
    for line in NEXRAD_TABLE.strip().splitlines():
        st, code, name, lat, lon, elev = line.split("|")
        out.append(RadarSite(code, name, float(lat), float(lon), float(elev),
                             network="US", region=st))
    return out


US_SITES = _nexrad_sites()
ALL_SITES = CH_SITES + US_SITES
SITE_BY_CODE = {s.code: s for s in ALL_SITES}

# ----------------------------------------------------------------------
# Products and color maps
# ----------------------------------------------------------------------

# NWS-style reflectivity steps (lower bound dBZ, color). Values under the
# user threshold are transparent; the faint grays below 5 dBZ only appear
# when the threshold is lowered.
REFL_STEPS = [
    (-30, "#3a3a4a"), (-20, "#4c4c60"), (-10, "#5e5e78"), (0, "#767690"),
    (5, "#04e9e7"), (10, "#019ff4"), (15, "#0300f4"), (20, "#02fd02"),
    (25, "#01c501"), (30, "#008e00"), (35, "#fdf802"), (40, "#e5bc00"),
    (45, "#fd9500"), (50, "#fd0000"), (55, "#d40000"), (60, "#bc0000"),
    (65, "#f800fd"), (70, "#9854c6"), (75, "#fdfdfd"),
]

# Diverging radial velocity stops on t = v / vmax (negative = toward radar).
VEL_STOPS = [
    (-1.00, "#00441b"), (-0.60, "#1a9850"), (-0.25, "#91cf60"),
    (-0.05, "#d9f0d3"), (0.00, "#9e9e9e"), (0.05, "#fddbc7"),
    (0.25, "#f46d43"), (0.60, "#d73027"), (1.00, "#67001f"),
]

# Sequential "turbo"-like stops on t in [0, 1] for dual-pol / width.
SEQ_STOPS = [
    (0.00, "#30123b"), (0.10, "#4145ab"), (0.20, "#4675ed"), (0.30, "#39a2fc"),
    (0.40, "#1bcfd4"), (0.50, "#24eca6"), (0.60, "#61fc6c"), (0.70, "#a4fc3b"),
    (0.80, "#f3c63a"), (0.90, "#f36315"), (1.00, "#7a0403"),
]


@dataclass
class Product:
    key: str      # ODIM quantity name
    name: str
    unit: str
    kind: str     # "refl" | "vel" | "seq"
    vmin: float = 0.0
    vmax: float = 1.0


PRODUCTS = [
    Product("DBZH", "Reflectivity", "dBZ", "refl", -32.0, 95.0),
    Product("TH", "Reflectivity (unfiltered)", "dBZ", "refl", -32.0, 95.0),
    Product("VRADH", "Radial velocity", "m/s", "vel"),
    Product("WRADH", "Spectrum width", "m/s", "seq", 0.0, 12.0),
    Product("ZDR", "Differential reflectivity", "dB", "seq", -2.0, 6.0),
    Product("RHOHV", "Correlation coefficient", "", "seq", 0.70, 1.00),
    Product("PHIDP", "Differential phase", "deg", "seq", 0.0, 360.0),
]
PRODUCT_BY_KEY = {p.key: p for p in PRODUCTS}


def _rgb(hex_color: str):
    c = QColor(hex_color)
    return c.red(), c.green(), c.blue()


def _argb(r, g, b, a=255) -> int:
    return (a << 24) | (r << 16) | (g << 8) | b


_REFL_LEVELS = np.array([lo for lo, _c in REFL_STEPS], dtype=np.float64)
_REFL_ARGB = np.array([_argb(*_rgb(c)) for _lo, c in REFL_STEPS], dtype=np.uint32)


def _stops(stops):
    return (np.array([t for t, _c in stops]),
            np.array([_rgb(c) for _t, c in stops], dtype=np.float64))


_VEL_T, _VEL_RGB = _stops(VEL_STOPS)
_SEQ_T, _SEQ_RGB = _stops(SEQ_STOPS)


def colorize(vals, product: Product, threshold: float, vmax: float) -> np.ndarray:
    """Vectorized physical values (NaN = no echo) -> ARGB32 uint32 array."""
    vals = np.asarray(vals, dtype=np.float64)
    bad = ~np.isfinite(vals)
    v = np.where(bad, 0.0, vals)
    if product.kind == "refl":
        idx = np.searchsorted(_REFL_LEVELS, v, side="right") - 1
        out = np.where(idx >= 0, _REFL_ARGB[np.clip(idx, 0, len(_REFL_ARGB) - 1)], 0)
        bad |= v < threshold
    else:
        if product.kind == "vel":
            vm = vmax if vmax > 0 else 30.0
            t, xs, rgbs = np.clip(v / vm, -1.0, 1.0), _VEL_T, _VEL_RGB
        else:
            t = np.clip((v - product.vmin) / (product.vmax - product.vmin), 0.0, 1.0)
            xs, rgbs = _SEQ_T, _SEQ_RGB
        rgb = [np.rint(np.interp(t, xs, rgbs[:, i])).astype(np.uint32) for i in range(3)]
        out = np.uint32(0xFF000000) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    return np.where(bad, 0, out).astype(np.uint32)


def product_range(product: Product, vmax: float):
    if product.kind == "vel":
        vm = vmax if vmax > 0 else 30.0
        return -vm, vm
    return product.vmin, product.vmax


# ----------------------------------------------------------------------
# Sweeps and volumes
# ----------------------------------------------------------------------


@dataclass
class Quantity:
    raw: np.ndarray        # (nrays, nbins) stored integers
    gain: float
    offset: float
    nodata: float
    undetect: float

    def value_at(self, ray: int, b: int):
        r = self.raw[ray, b]
        if r == self.nodata or r == self.undetect:
            return None
        return float(r) * self.gain + self.offset


@dataclass
class Sweep:
    """One PPI on a regular azimuth grid: row i covers azimuth
    [i, i+1) * 360/nrays degrees clockwise from north."""
    site_code: str
    lat: float
    lon: float
    height: float          # antenna height, m MSL
    elangle: float
    nrays: int
    nbins: int
    rscale: float          # m
    rstart: float          # m, start of the first bin
    start: datetime
    nyquist: float
    quantities: dict = field(default_factory=dict)
    source: str = ""

    @property
    def max_range(self) -> float:
        return self.rstart + self.nbins * self.rscale

    def geometry_key(self):
        return (round(self.lat, 5), round(self.lon, 5), self.height,
                round(self.elangle, 3), self.nrays, self.nbins,
                self.rscale, self.rstart)


class Volume:
    """A decoded data file: one or more sweeps."""

    site_code = ""

    def elevations(self) -> list:
        raise NotImplementedError

    def products(self) -> set:
        raise NotImplementedError

    def sweep(self, elev, product_key):
        raise NotImplementedError


class OdimVolume(Volume):
    def __init__(self, sweeps):
        self.sweeps = sweeps
        self.site_code = sweeps[0].site_code if sweeps else ""

    def elevations(self):
        return sorted({round(s.elangle, 1) for s in self.sweeps})

    def products(self):
        return {q for s in self.sweeps for q in s.quantities}

    def sweep(self, elev, product_key):
        cand = [s for s in self.sweeps if product_key in s.quantities]
        if not cand:
            return None
        if elev is None:
            elev = min(s.elangle for s in cand)
        return min(cand, key=lambda s: abs(s.elangle - elev))


# ---- ODIM HDF5 ---------------------------------------------------------


def _attr(group, name, default=None):
    if group is None or name not in group.attrs:
        return default
    v = group.attrs[name]
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.ndarray) and v.size == 1:
        v = v.item()
    if isinstance(v, np.generic):
        return v.item()
    return v


def _parse_dt(date_s, time_s):
    try:
        return datetime.strptime("%s%s" % (date_s, (time_s or "000000")[:6]),
                                 "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def read_odim(data, source="") -> OdimVolume:
    """Read an ODIM_H5 SCAN or PVOL (bytes or path)."""
    if h5py is None:
        raise RuntimeError("h5py is not installed (pip install h5py)")
    fh = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    sweeps = []
    with h5py.File(fh, "r") as f:
        what, where, how = f.get("what"), f.get("where"), f.get("how")
        src = _attr(what, "source", "") or ""
        m = re.search(r"NOD:([A-Za-z0-9]+)", src)
        code = m.group(1).lower() if m else "local"
        lat = float(_attr(where, "lat", 0.0))
        lon = float(_attr(where, "lon", 0.0))
        height = float(_attr(where, "height", 0.0))
        top_dt = _parse_dt(_attr(what, "date"), _attr(what, "time"))
        dnames = sorted((k for k in f.keys() if k.startswith("dataset")),
                        key=lambda k: int(re.sub(r"\D", "", k) or 0))
        for dn in dnames:
            ds = f[dn]
            dwhere, dwhat, dhow = ds.get("where"), ds.get("what"), ds.get("how")
            nrays = int(_attr(dwhere, "nrays", 0))
            nbins = int(_attr(dwhere, "nbins", 0))
            if not nrays or not nbins:
                continue
            sw = Sweep(
                site_code=code, lat=lat, lon=lon, height=height,
                elangle=float(_attr(dwhere, "elangle", 0.0)),
                nrays=nrays, nbins=nbins,
                rscale=float(_attr(dwhere, "rscale", 500.0)),
                rstart=float(_attr(dwhere, "rstart", 0.0)) * 1000.0,   # km -> m
                start=_parse_dt(_attr(dwhat, "startdate"), _attr(dwhat, "starttime"))
                if dwhat is not None and "startdate" in dwhat.attrs else top_dt,
                nyquist=float(_attr(dhow, "NI", _attr(how, "NI", 0.0)) or 0.0),
                source=source,
            )
            for qn in sorted(k for k in ds.keys() if k.startswith("data")):
                qg = ds[qn]
                if "data" not in qg:
                    continue
                qwhat = qg.get("what")
                qname = _attr(qwhat, "quantity", None) or _attr(dwhat, "quantity", qn)
                sw.quantities[qname] = Quantity(
                    raw=np.asarray(qg["data"][()]),
                    gain=float(_attr(qwhat, "gain", 1.0)),
                    offset=float(_attr(qwhat, "offset", 0.0)),
                    nodata=float(_attr(qwhat, "nodata", -1e30)),
                    undetect=float(_attr(qwhat, "undetect", -1e30)),
                )
            sweeps.append(sw)
    return OdimVolume(sweeps)


# ---- NEXRAD Level II ----------------------------------------------------

NEXRAD_MOMENTS = {b"REF": "DBZH", b"VEL": "VRADH", b"SW ": "WRADH",
                  b"ZDR": "ZDR", b"PHI": "PHIDP", b"RHO": "RHOHV"}
_M31_HDR = struct.Struct(">4sIHHfBBHBBBBfBBH")
_MOMENT_HDR = struct.Struct(">Hhh")          # ngates, first gate (m), spacing (m)


@dataclass
class _Cut:
    number: int
    azimuths: list = field(default_factory=list)
    elevs: list = field(default_factory=list)
    blocks: dict = field(default_factory=dict)   # quantity -> [block offset per radial or -1]
    az_res: int = 2
    date: int = 0
    ms: int = 0
    nyquist: float = 0.0

    @property
    def elangle(self):
        return float(np.mean(self.elevs)) if self.elevs else 0.0


class NexradVolume(Volume):
    """Indexed NEXRAD Archive II volume. Holds the decompressed buffer and
    extracts one (cut, moment) sweep on demand, so a 50+ MB volume never
    has to be fully unpacked into arrays."""

    def __init__(self, data: bytes, source=""):
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        if data[:4] != b"AR2V":
            raise ValueError("not a NEXRAD Archive II file")
        self.source = source
        self.site_code = data[20:24].decode("ascii", "replace").strip("\0 ")
        self.buf = self._decompress(data)
        self.cuts = OrderedDict()
        self.lat = self.lon = 0.0
        self.height = 0.0
        self.vcp = 0
        self._index()

    @staticmethod
    def _decompress(data: bytes) -> bytes:
        pos, parts = 24, []
        while pos + 4 <= len(data):
            n = abs(struct.unpack(">i", data[pos:pos + 4])[0])
            pos += 4
            if n == 0:
                break
            chunk = data[pos:pos + n]
            parts.append(bz2.decompress(chunk) if chunk[:2] == b"BZ" else chunk)
            pos += n
        if not parts:  # uncompressed legacy file: messages follow the header
            parts.append(data[24:])
        return b"".join(parts)

    def _index(self):
        buf, pos, end = self.buf, 0, len(self.buf)
        have_vol = False
        while pos + 28 <= end:
            size, _ch, mtype = struct.unpack(">HBB", buf[pos + 12:pos + 16])
            if mtype != 31:
                pos += 2432
                continue
            if size == 0:
                break
            h = pos + 28
            (_rid, ms, date, _azn, az, _comp, _sp, _rlen, az_res, _status, eln, _cutn,
             el, _blank, _mode, nblocks) = _M31_HDR.unpack_from(buf, h)
            nblocks = min(nblocks, 12)
            ptrs = struct.unpack_from(">%dI" % nblocks, buf, h + 32)
            cut = self.cuts.get(eln)
            if cut is None:
                cut = self.cuts[eln] = _Cut(eln, az_res=az_res, date=date, ms=ms)
            i = len(cut.azimuths)
            cut.azimuths.append(az)
            cut.elevs.append(el)
            for p in ptrs:
                b = h + p
                if b + 28 > end:
                    continue
                kind, name = buf[b:b + 1], buf[b + 1:b + 4]
                if kind == b"D":
                    q = NEXRAD_MOMENTS.get(name)
                    if q is not None:
                        lst = cut.blocks.setdefault(q, [])
                        if len(lst) < i:
                            lst.extend([-1] * (i - len(lst)))
                        lst.append(b)
                elif name == b"VOL" and not have_vol:
                    lat, lon, hgt, feed = struct.unpack_from(">ffhH", buf, b + 8)
                    self.lat, self.lon = lat, lon
                    self.height = float(hgt + feed)
                    self.vcp = struct.unpack_from(">H", buf, b + 40)[0]
                    have_vol = True
                elif name == b"RAD" and not cut.nyquist:
                    cut.nyquist = struct.unpack_from(">h", buf, b + 16)[0] / 100.0
            pos += 12 + size * 2
        for cut in self.cuts.values():
            n = len(cut.azimuths)
            for lst in cut.blocks.values():
                if len(lst) < n:
                    lst.extend([-1] * (n - len(lst)))

    def elevations(self):
        return sorted({round(c.elangle, 1) for c in self.cuts.values()})

    def products(self):
        return {q for c in self.cuts.values() for q in c.blocks}

    def _ngates(self, cut, q):
        for b in cut.blocks[q]:
            if b >= 0:
                return _MOMENT_HDR.unpack_from(self.buf, b + 8)[0]
        return 0

    def sweep(self, elev, product_key):
        cand = [c for c in self.cuts.values() if product_key in c.blocks]
        if not cand:
            return None
        if elev is None:
            elev = min(c.elangle for c in cand)
        # nearest elevation; among duplicate split cuts prefer the longest range
        cut = min(cand, key=lambda c: (round(abs(c.elangle - elev), 1),
                                       -self._ngates(c, product_key), c.number))
        return self._extract(cut, product_key)

    def _extract(self, cut: _Cut, q: str):
        buf = self.buf
        blocks = cut.blocks[q]
        rows = [i for i, b in enumerate(blocks) if b >= 0]
        if not rows:
            return None
        b0 = blocks[rows[0]]
        _ng, first, spacing = _MOMENT_HDR.unpack_from(buf, b0 + 8)
        word = buf[b0 + 19]
        scale, offset = struct.unpack_from(">ff", buf, b0 + 20)
        dtype = np.dtype(">u2") if word == 16 else np.dtype("u1")
        maxng = max(_MOMENT_HDR.unpack_from(buf, blocks[i] + 8)[0] for i in rows)
        raw = np.zeros((len(rows), maxng), dtype=np.uint16 if word == 16 else np.uint8)
        for r, i in enumerate(rows):
            b = blocks[i]
            ng = _MOMENT_HDR.unpack_from(buf, b + 8)[0]
            raw[r, :ng] = np.frombuffer(buf, dtype=dtype, count=ng, offset=b + 28)
        az = np.asarray(cut.azimuths, dtype=np.float64)[rows]

        # resample the radials onto a regular azimuth grid
        step = 0.5 if cut.az_res == 1 else 1.0
        nreg = int(round(360.0 / step))
        centers = (np.arange(nreg) + 0.5) * step
        order = np.argsort(az)
        saz = az[order]
        j = np.searchsorted(saz, centers) % len(saz)
        jm = (j - 1) % len(saz)
        d_j = np.abs((saz[j] - centers + 180.0) % 360.0 - 180.0)
        d_m = np.abs((saz[jm] - centers + 180.0) % 360.0 - 180.0)
        pick = np.where(d_j <= d_m, j, jm)
        dist = np.minimum(d_j, d_m)
        reg = raw[order][pick]
        reg[dist > step * 1.01] = 0

        if scale == 0:
            scale = 1.0
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        start = epoch + timedelta(days=cut.date - 1, milliseconds=cut.ms)
        sw = Sweep(site_code=self.site_code, lat=self.lat, lon=self.lon,
                   height=self.height, elangle=cut.elangle, nrays=nreg,
                   nbins=maxng, rscale=float(spacing),
                   rstart=float(first) - spacing / 2.0, start=start,
                   nyquist=cut.nyquist, source=self.source)
        # Level II: value = (raw - offset) / scale; 0 = below threshold, 1 = range folded
        sw.quantities[q] = Quantity(raw=reg, gain=1.0 / scale, offset=-offset / scale,
                                    nodata=1.0, undetect=0.0)
        return sw


def decode_file(data: bytes, source="") -> Volume:
    """Sniff the format and decode an ODIM HDF5 file or a NEXRAD Level II volume."""
    head = data[:8]
    if head.startswith(b"\x89HDF"):
        return read_odim(data, source)
    if head.startswith(b"AR2V") or head.startswith(b"\x1f\x8b"):
        return NexradVolume(data, source)
    raise ValueError("unrecognized file format (expected ODIM HDF5 or NEXRAD Level II)")


# ----------------------------------------------------------------------
# Data providers
# ----------------------------------------------------------------------


@dataclass
class ScanEntry:
    when: datetime     # nominal time from the file name
    elev: object       # float for single-sweep files, None for volumes
    key: str


class Provider:
    network = ""
    label = ""
    sources = []                 # [(label, base url)]
    product_keys = []
    per_elevation_files = False  # True: one file per sweep, elevation in the key
    default_range = 230_000.0
    lag_note = ""

    def prefixes(self, site: RadarSite, days):
        raise NotImplementedError

    def parse_key(self, key: str):
        raise NotImplementedError

    def decode(self, data: bytes, key: str) -> Volume:
        return decode_file(data, key)


class MeteoSwissProvider(Provider):
    network = "CH"
    label = "MeteoSwiss"
    # EUMETNET ORD 24-hour rolling cache. The primary bucket has been empty
    # during cloud-provider outages; ORD then serves from the backup bucket,
    # so "Automatic" tries each in turn until one returns files.
    sources = [
        ("ORD primary (CloudFerro)", "https://s3.waw3-1.cloudferro.com/openradar-24h"),
        ("ORD backup (ECMWF)", "https://object-store.os-api.cci1.ecmwf.int/openradar-24h-backup"),
    ]
    product_keys = ["DBZH", "TH", "VRADH"]
    per_elevation_files = True
    default_range = 230_000.0
    KEY_RE = re.compile(
        r"/(?P<site>[a-z0-9]+)@(?P<ts>\d{8}T\d{4})@(?P<el>-?\d+(?:\.\d+)?)@(?P<q>[^/@]+)\.h5$")

    def prefixes(self, site, days):
        return ["%s/CH/%s/SCAN/" % (d.strftime("%Y/%m/%d"), site.code) for d in days]

    def parse_key(self, key):
        m = self.KEY_RE.search(key)
        if not m:
            return None
        when = datetime.strptime(m.group("ts"), "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
        return ScanEntry(when, float(m.group("el")), key)


class NexradProvider(Provider):
    network = "US"
    label = "NEXRAD"
    sources = [("AWS Unidata NEXRAD Level II", "https://unidata-nexrad-level2.s3.amazonaws.com")]
    product_keys = ["DBZH", "VRADH", "WRADH", "ZDR", "RHOHV", "PHIDP"]
    per_elevation_files = False
    default_range = 300_000.0
    lag_note = "volumes post ~5-10 min after scan end"
    KEY_RE = re.compile(r"/(?P<site>[A-Z]{4})(?P<ts>\d{8}_\d{6})(?:_V\d+)?(?:\.gz)?$")

    def prefixes(self, site, days):
        return ["%s/%s/" % (d.strftime("%Y/%m/%d"), site.code) for d in days]

    def parse_key(self, key):
        m = self.KEY_RE.search(key)
        if not m:
            return None
        when = datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        return ScanEntry(when, None, key)


PROVIDERS = {"CH": MeteoSwissProvider(), "US": NexradProvider()}

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
REFRESH_MS = 120_000           # auto-refresh period for new scans
HISTORY = timedelta(hours=24)  # length of the rolling cache shown

# ----------------------------------------------------------------------
# Geometry: Web Mercator + radar beam propagation
# ----------------------------------------------------------------------

EARTH_R = 6371008.8
KE_R = EARTH_R * 4.0 / 3.0     # effective earth radius (4/3 refraction model)
TILE = 256


def lonlat_to_norm(lon, lat):
    """Lon/lat (deg) -> normalized Web Mercator [0,1] x [0,1]."""
    lat = max(-85.05112878, min(85.05112878, lat))
    x = (lon + 180.0) / 360.0
    s = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return x, y


def norm_to_lonlat(x, y):
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
    return lon, lat


def destination(lat, lon, bearing_deg, dist_m):
    """Great-circle destination point -> (lat, lon)."""
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    d = dist_m / EARTH_R
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def dist_bearing(lat1, lon1, lat2, lon2):
    """Great-circle distance (m) and bearing (deg) from point 1 to point 2.
    Works on scalars or numpy arrays (degrees)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    d = 2 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return d, (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def beam_height(slant_m, el_deg):
    """Beam centre height above the antenna (m) for slant range r."""
    e = np.radians(el_deg)
    return np.sqrt(slant_m ** 2 + KE_R ** 2 + 2 * slant_m * KE_R * np.sin(e)) - KE_R


def ground_range(slant_m, el_deg):
    """Ground (great-circle) distance (m) for slant range r."""
    e = np.radians(el_deg)
    h = beam_height(slant_m, el_deg)
    return KE_R * np.arcsin(slant_m * np.cos(e) / (KE_R + h))


class PolarLUT:
    """Maps a Web Mercator raster around a radar to flat (ray, bin) indices
    (-1 = outside the sweep). Built once per sweep geometry, so drawing a
    new sweep is a numpy gather plus a color lookup."""

    MIN_PX, MAX_PX = 1400, 2400

    def __init__(self, sw: Sweep):
        rng = float(ground_range(sw.max_range, sw.elangle)) * 1.01
        dlat = math.degrees(rng / EARTH_R)
        dlon = dlat / max(0.05, math.cos(math.radians(sw.lat)))
        x0, y0 = lonlat_to_norm(sw.lon - dlon, sw.lat + dlat)
        x1, y1 = lonlat_to_norm(sw.lon + dlon, sw.lat - dlat)
        self.bounds = (x0, y0, x1, y1)          # normalized mercator
        w = int(max(self.MIN_PX, min(self.MAX_PX, 2 * rng / (sw.rscale * 1.6))))
        h = max(1, int(round(w * (y1 - y0) / (x1 - x0))))
        self.width, self.height = w, h

        xs = x0 + (np.arange(w) + 0.5) / w * (x1 - x0)
        lons = xs * 360.0 - 180.0
        edges = sw.rstart + np.arange(sw.nbins + 1) * sw.rscale
        s_edges = ground_range(edges, sw.elangle)
        az_step = 360.0 / sw.nrays
        index = np.empty((h, w), dtype=np.int32)
        for r0 in range(0, h, 256):              # row chunks keep peak memory low
            r1 = min(h, r0 + 256)
            ys = y0 + (np.arange(r0, r1) + 0.5) / h * (y1 - y0)
            lats = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * ys))))
            lon_g, lat_g = np.meshgrid(lons, lats)
            dist, az = dist_bearing(sw.lat, sw.lon, lat_g, lon_g)
            b = np.searchsorted(s_edges, dist, side="right") - 1
            r = np.floor(az / az_step).astype(np.int64) % sw.nrays
            valid = (b >= 0) & (b < sw.nbins) & (dist >= s_edges[0])
            index[r0:r1] = np.where(valid, r * sw.nbins + b, -1)
        self.index = index.ravel()


_LUT_CACHE: "OrderedDict[tuple, PolarLUT]" = OrderedDict()
_LUT_LOCK = threading.Lock()


def get_lut(sw: Sweep) -> PolarLUT:
    key = sw.geometry_key()
    with _LUT_LOCK:
        lut = _LUT_CACHE.get(key)
        if lut is None:
            lut = PolarLUT(sw)
            _LUT_CACHE[key] = lut
            while len(_LUT_CACHE) > 10:
                _LUT_CACHE.popitem(last=False)
        else:
            _LUT_CACHE.move_to_end(key)
        return lut


def color_table(q: Quantity, product: Product, threshold: float, vmax: float):
    """256-entry ARGB32 palette indexed by a stored uint8 value."""
    raw = np.arange(256, dtype=np.float64)
    vals = raw * q.gain + q.offset
    vals[(raw == q.nodata) | (raw == q.undetect)] = np.nan
    return colorize(vals, product, threshold, vmax)


@dataclass
class Frame:
    sweep: Sweep
    image: QImage
    bounds: tuple
    elevations: list
    products: set


def render_sweep(sw: Sweep, product: Product, threshold: float):
    """Render one quantity of a sweep to an 8-bit indexed QImage (small
    enough to cache whole animation loops) plus its mercator bounds.
    Thread-safe: QImage may be built off the GUI thread."""
    q = sw.quantities.get(product.key)
    if q is None:
        return None, None
    lut = get_lut(sw)
    vmax = sw.nyquist if sw.nyquist > 0 else 30.0
    flat = q.raw.ravel()
    valid = lut.index >= 0
    gather = flat[np.where(valid, lut.index, 0)]
    palette = None
    if q.raw.dtype == np.uint8:
        table = color_table(q, product, threshold, vmax)
        clear = np.nonzero(table == 0)[0]
        if clear.size:
            idx = gather.copy()
            idx[~valid] = clear[0]
            palette = table
    if palette is None:
        # 16-bit (or no spare palette slot): quantize to 255 levels, 0 = clear
        phys = gather.astype(np.float64) * q.gain + q.offset
        phys[(gather == q.nodata) | (gather == q.undetect) | ~valid] = np.nan
        lo, hi = product_range(product, vmax)
        levels = 255
        t = np.clip((phys - lo) / (hi - lo), 0.0, 1.0)
        idx = (1 + np.rint(np.nan_to_num(t) * (levels - 1))).astype(np.uint8)
        idx[~np.isfinite(phys)] = 0
        centers = lo + np.arange(levels) / (levels - 1) * (hi - lo)
        palette = np.concatenate([[0], colorize(centers, product, threshold, vmax)])
    arr = np.ascontiguousarray(idx.reshape(lut.height, lut.width).astype(np.uint8))
    img = QImage(arr.data, lut.width, lut.height, lut.width, QImage.Format.Format_Indexed8)
    img.setColorTable([int(c) for c in palette])
    return img.copy(), lut.bounds


# ----------------------------------------------------------------------
# Basemaps
# ----------------------------------------------------------------------


@dataclass
class Basemap:
    name: str
    url: str                # {s} {z} {x} {y}
    attribution: str
    subdomains: str = ""
    labels_url: str = ""    # optional label-only layer drawn above radar
    max_zoom: int = 18
    background: str = "#1b1b1f"


# CARTO basemaps now need an API key, so the dark/light canvases come from
# Esri's public tile services (label layers are transparent PNG overlays).
_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/"
_ESRI_ATTR = "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors"

BASEMAPS = [
    Basemap("Esri Dark Gray",
            _ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
            _ESRI_ATTR,
            labels_url=_ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
            max_zoom=16, background="#1b1b1f"),
    Basemap("Esri Light Gray",
            _ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
            _ESRI_ATTR,
            labels_url=_ESRI + "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
            max_zoom=16, background="#e8e8e8"),
    Basemap("Esri Topographic",
            _ESRI + "World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
            _ESRI_ATTR, background="#d8d4cc"),
    Basemap("OpenStreetMap",
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "© OpenStreetMap contributors", background="#d8d4cc", max_zoom=19),
    Basemap("OpenTopoMap",
            "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
            "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)",
            "abc", max_zoom=17, background="#d8d4cc"),
    Basemap("swisstopo Grey (CH)",
            "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg",
            "© swisstopo", background="#cfcfcf"),
    Basemap("None", "", "", background="#202024"),
]
BASEMAP_BY_NAME = {b.name: b for b in BASEMAPS}

DATA_ATTRIB = {
    "CH": "Radar © MeteoSwiss (CC BY 4.0) via EUMETNET ORD",
    "US": "Radar: NOAA NWS NEXRAD Level II via Unidata/AWS",
}

# ----------------------------------------------------------------------
# Map widget
# ----------------------------------------------------------------------


class MapWidget(QWidget):
    """Slippy map: basemap tiles, radar raster, rings, sites, legend."""

    hovered = pyqtSignal(float, float)       # lon, lat
    left = pyqtSignal()
    siteClicked = pyqtSignal(str)
    viewChanged = pyqtSignal()

    MIN_ZOOM, MAX_ZOOM = 2.0, 16.0
    TILE_CACHE_MAX = 600

    def __init__(self, nam: QNetworkAccessManager, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.nam = nam
        self.cx, self.cy = lonlat_to_norm(8.23, 46.80)
        self.zoom = 7.0
        self.basemap = BASEMAPS[0]
        self.show_labels = True
        self.show_rings = True
        self.show_sites = True
        self.show_legend = True
        self.smooth = False
        self.opacity = 0.85
        self.radar_pm = None
        self.radar_bounds = None
        self.product = PRODUCTS[0]
        self.threshold = 5.0
        self.vmax = 30.0
        self.site = None
        self.max_range = 0.0
        self.info_text = ""
        self.message = ""
        self._tiles = OrderedDict()   # (template, z, x, y) -> QPixmap
        self._pending = set()
        self._failed = {}
        self._drag = None
        self._press_pos = None
        self._ring_cache = {}

    # ---- coordinates --------------------------------------------------
    def world_size(self):
        return TILE * (2.0 ** self.zoom)

    def norm_to_screen(self, x, y):
        ws = self.world_size()
        return QPointF((x - self.cx) * ws + self.width() / 2.0,
                       (y - self.cy) * ws + self.height() / 2.0)

    def screen_to_norm(self, px, py):
        ws = self.world_size()
        return (self.cx + (px - self.width() / 2.0) / ws,
                self.cy + (py - self.height() / 2.0) / ws)

    def lonlat_to_screen(self, lon, lat):
        return self.norm_to_screen(*lonlat_to_norm(lon, lat))

    def center_on(self, lon, lat, zoom=None):
        self.cx, self.cy = lonlat_to_norm(lon, lat)
        if zoom is not None:
            self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        self.update()
        self.viewChanged.emit()

    def fit_range(self, site: RadarSite, range_m: float):
        """Zoom so that a radius around the site fills the view."""
        span = 2.2 * range_m / (EARTH_R * math.cos(math.radians(site.lat)) * 2 * math.pi)
        px = max(100, min(self.width(), self.height()))
        z = math.log2(px / (span * TILE))
        self.center_on(site.lon, site.lat, round(z * 4) / 4)

    def is_visible(self, lon, lat, margin=40):
        p = self.lonlat_to_screen(lon, lat)
        return (margin <= p.x() <= self.width() - margin
                and margin <= p.y() <= self.height() - margin)

    def zoom_at(self, delta_log2, pos=None):
        old = self.zoom
        new = max(self.MIN_ZOOM, min(self.MAX_ZOOM, old + delta_log2))
        if new == old:
            return
        if pos is None:
            pos = QPointF(self.width() / 2.0, self.height() / 2.0)
        nx, ny = self.screen_to_norm(pos.x(), pos.y())
        self.zoom = new
        ws = self.world_size()
        self.cx = nx - (pos.x() - self.width() / 2.0) / ws
        self.cy = ny - (pos.y() - self.height() / 2.0) / ws
        self.update()
        self.viewChanged.emit()

    # ---- data ---------------------------------------------------------
    def set_radar(self, img, bounds, product, vmax, info=""):
        self.radar_pm = QPixmap.fromImage(img) if img is not None else None
        self.radar_bounds = bounds
        self.product, self.vmax, self.info_text = product, vmax, info
        self.message = ""
        self.update()

    def clear_radar(self, message=""):
        self.radar_pm = self.radar_bounds = None
        self.info_text = ""
        self.message = message
        self.update()

    def set_site(self, site, max_range):
        self.site, self.max_range = site, max_range
        self.update()

    def set_basemap(self, name):
        self.basemap = BASEMAP_BY_NAME.get(name, BASEMAPS[0])
        self.update()

    # ---- tiles --------------------------------------------------------
    def _tile_url(self, template, z, x, y):
        bm = self.basemap
        s = bm.subdomains[(x + y) % len(bm.subdomains)] if bm.subdomains else ""
        return template.format(s=s, z=z, x=x, y=y)

    def _get_tile(self, template, z, x, y, request=True):
        key = (template, z, x, y)
        pm = self._tiles.get(key)
        if pm is not None:
            self._tiles.move_to_end(key)
            return pm
        if request and key not in self._pending:
            failed_at = self._failed.get(key)
            if failed_at and time.time() - failed_at < 60:
                return None
            self._pending.add(key)
            req = QNetworkRequest(QUrl(self._tile_url(template, z, x, y)))
            req.setRawHeader(b"User-Agent", USER_AGENT.encode())
            req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                             QNetworkRequest.CacheLoadControl.PreferCache)
            req.setTransferTimeout(20000)
            reply = self.nam.get(req)
            reply.finished.connect(lambda r=reply, k=key: self._tile_done(r, k))
        return None

    def _tile_done(self, reply, key):
        self._pending.discard(key)
        if reply.error() == QNetworkReply.NetworkError.NoError:
            pm = QPixmap()
            if pm.loadFromData(reply.readAll()):
                self._tiles[key] = pm
                while len(self._tiles) > self.TILE_CACHE_MAX:
                    self._tiles.popitem(last=False)
                self.update()
            else:
                self._failed[key] = time.time()
        else:
            self._failed[key] = time.time()
        reply.deleteLater()

    def _draw_tile_layer(self, p: QPainter, template):
        if not template:
            return
        tz = int(max(0, min(self.basemap.max_zoom, round(self.zoom))))
        n = 1 << tz
        tsize = TILE * 2.0 ** (self.zoom - tz)
        x0n, y0n = self.screen_to_norm(0, 0)
        x1n, y1n = self.screen_to_norm(self.width(), self.height())
        tx0, tx1 = int(math.floor(x0n * n)), int(math.floor(x1n * n))
        ty0, ty1 = max(0, int(math.floor(y0n * n))), min(n - 1, int(math.floor(y1n * n)))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                wx = tx % n
                origin = self.norm_to_screen(tx / n, ty / n)
                target = QRectF(origin.x(), origin.y(), tsize + 0.5, tsize + 0.5)
                pm = self._get_tile(template, tz, wx, ty)
                if pm is not None:
                    p.drawPixmap(target, pm, QRectF(pm.rect()))
                    continue
                # fall back to a cached ancestor tile while loading
                for up in range(1, 6):
                    pz = tz - up
                    if pz < 0:
                        break
                    px, py = wx >> up, ty >> up
                    ppm = self._get_tile(template, pz, px, py, request=False)
                    if ppm is None:
                        continue
                    k = 1 << up
                    sub = ppm.width() / k
                    src = QRectF((wx - px * k) * sub, (ty - py * k) * sub, sub, sub)
                    p.drawPixmap(target, ppm, src)
                    break

    # ---- painting -----------------------------------------------------
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.basemap.background))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._draw_tile_layer(p, self.basemap.url)

        if self.radar_pm is not None and self.radar_bounds is not None:
            x0, y0, x1, y1 = self.radar_bounds
            a, b = self.norm_to_screen(x0, y0), self.norm_to_screen(x1, y1)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
            p.setOpacity(self.opacity)
            p.drawPixmap(QRectF(a, b), self.radar_pm, QRectF(self.radar_pm.rect()))
            p.setOpacity(1.0)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if self.show_labels and self.basemap.labels_url:
            self._draw_tile_layer(p, self.basemap.labels_url)

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.show_rings and self.site is not None:
            self._draw_rings(p)
        if self.show_sites:
            self._draw_sites(p)
        if self.show_legend:
            self._draw_legend(p)
        self._draw_scale_bar(p)
        self._draw_overlay_text(p)
        p.end()

    def _ring_points(self, site, radius):
        key = (site.code, radius)
        pts = self._ring_cache.get(key)
        if pts is None:
            pts = []
            for b in range(0, 361, 3):
                lat, lon = destination(site.lat, site.lon, b, radius)
                pts.append(lonlat_to_norm(lon, lat))
            self._ring_cache[key] = pts
        return pts

    def _draw_rings(self, p: QPainter):
        site = self.site
        dark = QColor(self.basemap.background).lightness() < 128
        col = QColor(255, 255, 255, 110) if dark else QColor(0, 0, 0, 110)
        pen = QPen(col, 1.0, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        max_r = self.max_range or 230_000
        step = 50_000 if max_r <= 260_000 else 100_000
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 2))
        p.setFont(f)
        r = step
        while r <= max_r + 1:
            poly = QPolygonF([self.norm_to_screen(x, y) for x, y in self._ring_points(site, r)])
            p.drawPolyline(poly)
            lab = poly[0]
            p.drawText(QPointF(lab.x() + 3, lab.y() + 12), "%d km" % (r // 1000))
            r += step
        pen.setStyle(Qt.PenStyle.DotLine)          # azimuth spokes every 30 deg
        p.setPen(pen)
        c = self.lonlat_to_screen(site.lon, site.lat)
        for b in range(0, 360, 30):
            lat, lon = destination(site.lat, site.lon, b, max_r)
            p.drawLine(c, self.lonlat_to_screen(lon, lat))

    def _site_label(self, s, sel):
        if s.network == "CH":
            return s.name
        if sel or self.zoom >= 7:
            return "%s %s" % (s.code, s.name)
        if self.zoom >= 5:
            return s.code
        return ""

    def _draw_sites(self, p: QPainter):
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(max(7.0, f.pointSizeF() - (1 if self.zoom < 7 else 0)))
        p.setFont(f)
        w, h = self.width(), self.height()
        for s in ALL_SITES:
            pt = self.lonlat_to_screen(s.lon, s.lat)
            if not (-50 <= pt.x() <= w + 50 and -20 <= pt.y() <= h + 20):
                continue
            sel = self.site is not None and s.code == self.site.code
            size = 7 if sel else (5 if self.zoom >= 5 else 4)
            tri = QPolygonF([QPointF(pt.x(), pt.y() - size),
                             QPointF(pt.x() - size, pt.y() + size * 0.8),
                             QPointF(pt.x() + size, pt.y() + size * 0.8)])
            p.setPen(QPen(QColor("black"), 1.2))
            p.setBrush(QColor("#ffcc00") if sel else QColor("#ffffff"))
            p.drawPolygon(tri)
            label = self._site_label(s, sel)
            if not label:
                continue
            path = QPainterPath()
            path.addText(QPointF(pt.x() + size + 3, pt.y() + 4), f, label)
            p.setPen(QPen(QColor(0, 0, 0, 200), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ffcc00") if sel else QColor("white"))
            p.drawPath(path)

    def _legend_entries(self):
        prod = self.product
        if prod.kind == "refl":
            return [(lo, QColor(c)) for lo, c in REFL_STEPS if lo >= self.threshold - 4.999]
        lo, hi = product_range(prod, self.vmax)
        vals = np.linspace(lo, hi, 11)
        cols = colorize(vals, prod, self.threshold, self.vmax)
        return [(float(v), QColor.fromRgba(int(c))) for v, c in zip(vals, cols)]

    def _draw_legend(self, p: QPainter):
        if self.radar_pm is None:
            return
        entries = self._legend_entries()
        if not entries:
            return
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        fm = p.fontMetrics()
        box_h = 14
        unit = " [%s]" % self.product.unit if self.product.unit else ""
        title = self.product.key + unit
        w = max(fm.horizontalAdvance(title), 60) + 16
        h = box_h * len(entries) + fm.height() + 14
        x, y = 10, self.height() - h - 30
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.setPen(QColor("white"))
        p.drawText(QPointF(x + 8, y + fm.ascent() + 5), title)
        span = abs(entries[-1][0] - entries[0][0])
        fmt = "%d" if self.product.kind == "refl" or span > 40 else ("%.2f" if span <= 2 else "%.1f")
        yy = y + fm.height() + 8
        for v, col in reversed(entries):
            p.fillRect(QRectF(x + 8, yy, 18, box_h - 1), col)
            p.drawText(QPointF(x + 32, yy + box_h - 3), fmt % v)
            yy += box_h

    def _draw_scale_bar(self, p: QPainter):
        _lon, lat = norm_to_lonlat(self.cx, self.cy)
        m_per_px = math.cos(math.radians(lat)) * 2 * math.pi * EARTH_R / self.world_size()
        target = 120 * m_per_px
        nice = 10 ** math.floor(math.log10(target))
        for mult in (5, 2, 1):
            if mult * nice <= target:
                nice *= mult
                break
        px = nice / m_per_px
        x, y = self.width() - px - 16, self.height() - 34
        p.setPen(QPen(QColor("white"), 3))
        p.drawLine(QPointF(x, y), QPointF(x + px, y))
        p.setPen(QPen(QColor("black"), 1.5))
        p.drawLine(QPointF(x, y), QPointF(x + px, y))
        label = "%g km" % (nice / 1000) if nice >= 1000 else "%d m" % nice
        dark = QColor(self.basemap.background).lightness() < 128
        p.setPen(QColor("white") if dark else QColor("black"))
        p.drawText(QPointF(x, y - 5), label)

    def _draw_overlay_text(self, p: QPainter):
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 2))
        p.setFont(f)
        fm = p.fontMetrics()
        net = self.site.network if self.site is not None else "CH"
        attrib = " | ".join(t for t in (DATA_ATTRIB.get(net, ""), self.basemap.attribution) if t)
        w = fm.horizontalAdvance(attrib) + 10
        rect = QRectF(self.width() - w, self.height() - fm.height() - 4, w, fm.height() + 4)
        p.fillRect(rect, QColor(255, 255, 255, 170))
        p.setPen(QColor("#222"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, attrib)

        text = self.info_text or self.message
        if text:
            f2 = QFont(self.font())
            f2.setBold(True)
            p.setFont(f2)
            fm2 = p.fontMetrics()
            lines = text.split("\n")
            w2 = max(fm2.horizontalAdvance(t) for t in lines) + 16
            h2 = fm2.height() * len(lines) + 10
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(10, 10, w2, h2), 6, 6)
            p.setPen(QColor("white"))
            for i, t in enumerate(lines):
                p.drawText(QPointF(18, 15 + fm2.ascent() + i * fm2.height()), t)

    # ---- interaction --------------------------------------------------
    def _site_at(self, pos):
        best, best_d = None, 12.0
        for s in ALL_SITES:
            d = (self.lonlat_to_screen(s.lon, s.lat) - QPointF(pos)).manhattanLength()
            if d < best_d:
                best, best_d = s, d
        return best

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = (ev.position(), self.cx, self.cy)
            self._press_pos = ev.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._drag is not None:
            start, cx, cy = self._drag
            ws = self.world_size()
            self.cx = cx - (pos.x() - start.x()) / ws
            self.cy = min(1.0, max(0.0, cy - (pos.y() - start.y()) / ws))
            self.update()
        nx, ny = self.screen_to_norm(pos.x(), pos.y())
        lon, lat = norm_to_lonlat(nx, ny)
        self.hovered.emit((lon + 540) % 360 - 180, lat)
        if self._drag is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._site_at(pos)
                           else Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            moved = (ev.position() - self._press_pos).manhattanLength() if self._press_pos else 99
            self._drag = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            if moved < 4:
                s = self._site_at(ev.position())
                if s is not None:
                    self.siteClicked.emit(s.code)
            else:
                self.viewChanged.emit()

    def mouseDoubleClickEvent(self, ev):
        self.zoom_at(1.0, ev.position())

    def wheelEvent(self, ev):
        dy = ev.angleDelta().y()
        if dy:
            self.zoom_at(dy / 120.0 * 0.5, ev.position())

    def leaveEvent(self, _ev):
        self.left.emit()

    def keyPressEvent(self, ev):
        k = ev.key()
        step = 80 / self.world_size()
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(0.5)
        elif k == Qt.Key.Key_Minus:
            self.zoom_at(-0.5)
        elif k == Qt.Key.Key_Left:
            self.cx -= step
        elif k == Qt.Key.Key_Right:
            self.cx += step
        elif k == Qt.Key.Key_Up:
            self.cy -= step
        elif k == Qt.Key.Key_Down:
            self.cy += step
        else:
            super().keyPressEvent(ev)
            return
        self.update()


# ----------------------------------------------------------------------
# Navigation panel
# ----------------------------------------------------------------------


class NavPanel(QWidget):
    """Left-hand explorer panel: sites, products, elevations, times, display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(230)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        # Sites
        g = QGroupBox("Radar Sites")
        gl = QVBoxLayout(g)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter sites (name, code, state)...")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        gl.addWidget(self.filter)
        self.sites = QTreeWidget()
        self.sites.setHeaderLabels(["Site", "Code"])
        self.sites.setUniformRowHeights(True)
        self.sites.setColumnWidth(0, 150)
        self.sites.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sites.setMinimumHeight(230)
        self._site_items = {}
        for net, label in NETWORK_NAMES.items():
            root = QTreeWidgetItem([label, ""])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.sites.addTopLevelItem(root)
            if net == "CH":
                for s in CH_SITES:
                    root.addChild(self._site_item(s))
                root.setExpanded(True)
            else:
                groups = OrderedDict()
                for s in sorted(US_SITES, key=lambda s: (US_REGIONS.get(s.region, s.region), s.name)):
                    groups.setdefault(s.region, []).append(s)
                for region in sorted(groups, key=lambda r: US_REGIONS.get(r, r)):
                    gi = QTreeWidgetItem(["%s (%s)" % (US_REGIONS.get(region, region), region),
                                          str(len(groups[region]))])
                    gi.setFlags(gi.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    root.addChild(gi)
                    for s in groups[region]:
                        gi.addChild(self._site_item(s))
        gl.addWidget(self.sites)
        lay.addWidget(g, 2)

        # Products
        g = QGroupBox("Product")
        gl = QVBoxLayout(g)
        self.products = QListWidget()
        gl.addWidget(self.products)
        lay.addWidget(g)

        # Elevations
        g = QGroupBox("Elevation")
        gl = QVBoxLayout(g)
        self.elevations = QListWidget()
        self.elevations.setFixedHeight(120)
        gl.addWidget(self.elevations)
        lay.addWidget(g)

        # Times
        g = QGroupBox("Scan Time (UTC)")
        gl = QVBoxLayout(g)
        self.follow_latest = QCheckBox("Follow latest scan")
        self.follow_latest.setChecked(True)
        gl.addWidget(self.follow_latest)
        self.times = QListWidget()
        self.times.setMinimumHeight(150)
        gl.addWidget(self.times)
        lay.addWidget(g, 1)

        # Display
        g = QGroupBox("Display")
        form = QFormLayout(g)
        self.basemap = QComboBox()
        for b in BASEMAPS:
            self.basemap.addItem(b.name)
        form.addRow("Basemap:", self.basemap)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(10, 100)
        self.opacity.setValue(85)
        form.addRow("Opacity:", self.opacity)
        self.threshold = QSpinBox()
        self.threshold.setRange(-30, 60)
        self.threshold.setSingleStep(5)
        self.threshold.setValue(5)
        self.threshold.setSuffix(" dBZ")
        self.threshold.setToolTip("Hide reflectivity below this value")
        form.addRow("Min refl.:", self.threshold)
        self.rings = QCheckBox("Range rings")
        self.rings.setChecked(True)
        self.labels = QCheckBox("Map labels on top")
        self.labels.setChecked(True)
        self.legend = QCheckBox("Legend")
        self.legend.setChecked(True)
        self.smooth = QCheckBox("Smooth pixels")
        for w in (self.rings, self.labels, self.legend, self.smooth):
            form.addRow(w)
        lay.addWidget(g)

    def _site_item(self, s: RadarSite):
        it = QTreeWidgetItem([s.name, s.code])
        it.setData(0, Qt.ItemDataRole.UserRole, s.code)
        it.setToolTip(0, "%s (%s)  %.4f°  %.4f°  %d m%s"
                      % (s.name, s.code, s.lat, s.lon, s.height,
                         "  WMO %s" % s.wmo if s.wmo else ""))
        self._site_items[s.code] = it
        return it

    def _apply_filter(self, text):
        text = text.strip().lower()

        def visit(item):
            code = item.data(0, Qt.ItemDataRole.UserRole)
            if code:
                s = SITE_BY_CODE[code]
                hay = " ".join((s.name, s.code, s.region, US_REGIONS.get(s.region, ""))).lower()
                show = not text or text in hay
                item.setHidden(not show)
                return show
            any_child = False
            for i in range(item.childCount()):
                any_child |= visit(item.child(i))
            label_hit = bool(text) and text in item.text(0).lower()
            if label_hit:  # a matching group shows all of its children
                for i in range(item.childCount()):
                    item.child(i).setHidden(False)
                    for j in range(item.child(i).childCount()):
                        item.child(i).child(j).setHidden(False)
            show = any_child or label_hit or not text
            item.setHidden(not show)
            if text and show:
                item.setExpanded(True)
            return show

        for i in range(self.sites.topLevelItemCount()):
            visit(self.sites.topLevelItem(i))

    def select_site(self, code):
        it = self._site_items.get(code)
        if it is None:
            return
        self.sites.blockSignals(True)
        p = it.parent()
        while p is not None:
            p.setExpanded(True)
            p = p.parent()
        self.sites.setCurrentItem(it)
        self.sites.scrollToItem(it)
        self.sites.blockSignals(False)

    def set_products(self, keys, current):
        self.products.blockSignals(True)
        self.products.clear()
        for k in keys:
            prod = PRODUCT_BY_KEY[k]
            it = QListWidgetItem("%s  (%s)" % (prod.name, prod.key))
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.products.addItem(it)
        self.products.setFixedHeight(max(1, self.products.sizeHintForRow(0)) * max(3, len(keys)) + 8)
        if current in keys:
            self.products.setCurrentRow(keys.index(current))
        self.products.blockSignals(False)

    def select_product(self, key):
        for i in range(self.products.count()):
            if self.products.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.products.blockSignals(True)
                self.products.setCurrentRow(i)
                self.products.blockSignals(False)
                return


# ----------------------------------------------------------------------
# About dialog
# ----------------------------------------------------------------------


def _build_date() -> str:
    """Release/build date, derived from this file's mtime -- set-version.bash
    rewrites the VERSION line on every release, so this tracks the last publish."""
    try:
        return datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def resource_path(name: str) -> Path:
    """Locate a bundled resource both when run from source and when frozen
    by PyInstaller, which unpacks --add-data into _MEIPASS."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def app_icon() -> QIcon:
    path = resource_path("icon.png")
    return QIcon(str(path)) if path.is_file() else QIcon()


def _bold_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setBold(True)
    lbl.setFont(f)
    return lbl


_DIALOG_WIDTH = 440
_ANIM_MAX_W = _DIALOG_WIDTH - 32


def _animation_path() -> Path:
    return resource_path(os.path.join("screens", "landenlabs_400.webp"))


def _animation_display_size(path: Path) -> QSize:
    """Display size that preserves the animation's native aspect ratio."""
    native = QImageReader(str(path)).size()
    if not native.isValid() or native.width() == 0:
        return QSize(_ANIM_MAX_W, _ANIM_MAX_W)
    scale = min(1.0, _ANIM_MAX_W / native.width())
    return QSize(int(native.width() * scale), int(native.height() * scale))


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About %s" % APP_NAME)
        self.setModal(True)
        self.setFixedWidth(_DIALOG_WIDTH)
        self._movie = None
        self._final_pixmap = None
        self._last_frame_num = -1

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # Animated logo (plays once, then freezes on the last frame).
        self._anim_label = QLabel()
        self._anim_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        anim_path = _animation_path()
        if anim_path.exists():
            size = _animation_display_size(anim_path)
            self._anim_label.setFixedSize(size)
            self._movie = QMovie(str(anim_path))
            self._movie.setScaledSize(size)
            self._anim_label.setMovie(self._movie)
            self._movie.frameChanged.connect(self._on_frame_changed)
            root.addWidget(self._anim_label, alignment=Qt.AlignmentFlag.AlignCenter)

        header = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(app_icon().pixmap(48, 48))
        header.addWidget(icon_lbl)
        name_font = QFont()
        name_font.setPointSize(15)
        name_font.setBold(True)
        name_lbl = QLabel(APP_NAME)
        name_lbl.setFont(name_font)
        header.addWidget(name_lbl)
        header.addStretch(1)
        root.addLayout(header)

        desc = QLabel(
            "%s  —  Map viewer for single-site weather radar sweeps from the "
            "MeteoSwiss network (EUMETNET Open Radar Data) and the US NWS "
            "NEXRAD WSR-88D network (Level II)." % VERSION)
        desc.setWordWrap(True)
        root.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(5)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow(_bold_label("Author:"), QLabel("Dennis Lang"))
        form.addRow(_bold_label("Built:"), QLabel(_build_date()))
        form.addRow(_bold_label("Data:"), QLabel("MeteoSwiss (CC BY 4.0) via EUMETNET ORD;\n"
                                                 "NOAA NWS NEXRAD Level II via Unidata/AWS"))
        form.addRow(QLabel(""), QLabel("Created by LanDen Labs (2026)"))
        link = QLabel('<a href="https://landenlabs.com">https://landenlabs.com</a>')
        link.setOpenExternalLinks(True)
        link.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(_bold_label("Web:"), link)
        root.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        if self._movie is not None:
            self._last_frame_num = -1
            self._final_pixmap = None
            self._movie.start()

    def _on_frame_changed(self, frame_num: int):
        # QMovie has no reliable "play once" for animated WebP; detect the
        # wrap back to frame 0 and freeze on the cached final frame.
        if self._movie is None:
            return
        if frame_num == 0 and self._last_frame_num > 0:
            self._movie.stop()
            if self._final_pixmap is not None:
                self._anim_label.setMovie(None)
                self._anim_label.setPixmap(self._final_pixmap)
            return
        self._final_pixmap = self._movie.currentPixmap()
        self._last_frame_num = frame_num


_HELP_HTML = """
<h2>%s</h2>
<p>Shows single-site PPI sweeps from two radar networks:</p>
<ul>
<li><b>MeteoSwiss (CH)</b> &mdash; five C-band dual-pol radars, five elevations
(-0.2° to 2.5°) every 5 minutes, from the EUMETNET <i>Open Radar Data</i>
24-hour cache.</li>
<li><b>NWS NEXRAD (USA)</b> &mdash; 159 WSR-88D S-band radars (incl. Alaska,
Hawaii, Guam, Puerto Rico and DoD sites). Full Level II volumes (VCP-dependent,
typically 9&ndash;17 cuts every 4&ndash;10 minutes) from the Unidata/NOAA AWS
bucket. Volumes appear there 5&ndash;10 minutes after the scan.</li>
</ul>
<h3>Navigation panel</h3>
<ul>
<li><b>Radar Sites</b> &mdash; pick a radar, type in the filter box (name, ICAO code
or state), or click a triangle on the map.</li>
<li><b>Product</b> &mdash; reflectivity, radial velocity (green = toward the radar,
red = away); NEXRAD adds spectrum width, ZDR, correlation coefficient and PhiDP.</li>
<li><b>Elevation</b> &mdash; antenna elevation of the sweep. NEXRAD elevations
come from the volume coverage pattern of the scan being shown.</li>
<li><b>Scan Time</b> &mdash; any scan of the last 24 h. With
<i>Follow latest scan</i> on, new scans appear automatically.</li>
</ul>
<h3>Map</h3>
<ul>
<li>Drag to pan, mouse wheel / double-click / +/- to zoom.</li>
<li>The status bar shows the value, range, azimuth and beam height under the cursor.</li>
<li>Toggle the navigation panel with <b>Ctrl+B</b>; find a site with <b>Ctrl+F</b>.</li>
<li>Animate the loop with <b>Space</b>; step frames with <b>[</b> and <b>]</b>.</li>
</ul>
<h3>Data</h3>
<p>MeteoSwiss radar data &copy; MeteoSwiss, CC BY 4.0, distributed by EUMETNET MeteoGate
(<a href="https://opendatadocs.meteoswiss.ch/d-radar-data/d5-polar-3d-radar-products">docs</a>).
NEXRAD Level II data: NOAA National Weather Service, via the
<a href="https://registry.opendata.aws/noaa-nexrad/">NOAA Open Data Dissemination</a> program.</p>
<p>Local files (ODIM HDF5 SCAN/PVOL or NEXRAD Level II) open with File &rarr; Open.</p>
""" % APP_NAME


# ----------------------------------------------------------------------
# Background work
# ----------------------------------------------------------------------


class AsyncRunner(QObject):
    """Runs functions on a thread pool and delivers results on the GUI thread."""

    _done = pyqtSignal(object, object, object)   # callback, result, error

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=max(2, min(4, os.cpu_count() or 2)))
        self._done.connect(lambda cb, res, err: cb(res, err))

    def submit(self, fn, callback):
        def finished(fut):
            try:
                self._done.emit(callback, fut.result(), None)
            except Exception as exc:  # delivered to the callback
                self._done.emit(callback, None, exc)
        self.pool.submit(fn).add_done_callback(finished)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, open_path=None):
        super().__init__()
        self.setWindowTitle("%s - %s - LanDen Labs 2026" % (APP_NAME, VERSION))
        self.setWindowIcon(app_icon())
        self.settings = QSettings(ORG_NAME, "RadarViewer")

        cache_root = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation) or Path.home() / ".cache" / "radar-viewer")
        self.data_dir = cache_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._prune_disk_cache()

        self.nam = QNetworkAccessManager(self)
        disk = QNetworkDiskCache(self)
        disk.setCacheDirectory(str(cache_root / "tiles"))
        disk.setMaximumCacheSize(200 * 1024 * 1024)
        self.nam.setCache(disk)
        self.runner = AsyncRunner(self)

        # state
        self.site = CH_SITES[0]
        self.product = PRODUCTS[0]
        self.elev = None                  # selected elevation (deg) or None = lowest
        self.ch_source_mode = -1          # MeteoSwiss: -1 automatic, else source index
        self.source_idx = {}              # network -> index of the source in use
        self.entries = {}                 # elev (or None) -> [ScanEntry] ascending
        self.times = []                   # entries for the selected elevation
        self.time_idx = -1
        self.current = None               # displayed Sweep
        self.local = None                 # (Volume, ScanEntry) when viewing a local file
        self._vol_cache = OrderedDict()   # key -> Volume
        self._sweep_cache = OrderedDict()  # (key, elev, product) -> Sweep
        self._img_cache = OrderedDict()   # (key, elev, product, threshold) -> Frame
        self._meta = {}                   # key -> (elevations, products)
        self._vol_waiters = {}
        self._frame_waiters = {}
        self._listing_gen = 0
        self._downloads = 0

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_listing)
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._anim_step)

        self._build_ui()
        self._build_menus()
        self._restore_settings()
        self.refresh_timer.start(REFRESH_MS)

        if h5py is None:
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                self, APP_NAME, "The h5py package is required for MeteoSwiss data.\n\n"
                                "pip install h5py"))
        if open_path:
            QTimer.singleShot(0, lambda: self.open_local(open_path))
        else:
            QTimer.singleShot(0, lambda: self.select_site(self.site.code, force=True))

    @property
    def provider(self) -> Provider:
        return PROVIDERS[self.site.network]

    # ---- UI construction ----------------------------------------------
    def _build_ui(self):
        self.map = MapWidget(self.nam)
        self.nav = NavPanel()

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.nav)
        self.splitter.addWidget(self.map)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, True)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([280, 1000])
        self.splitter.splitterMoved.connect(self._sync_nav_action)
        self.setCentralWidget(self.splitter)

        tb = QToolBar("Timeline")
        tb.setObjectName("timeline")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.timeline_bar = tb
        st = self.style()
        self.act_nav = QAction("Navigation", self)
        self.act_nav.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        self.act_nav.setCheckable(True)
        self.act_nav.setChecked(True)
        self.act_nav.setShortcut(QKeySequence("Ctrl+B"))
        self.act_nav.setToolTip("Show / hide the navigation panel (Ctrl+B)")
        self.act_nav.toggled.connect(self.set_nav_visible)
        tb.addAction(self.act_nav)
        tb.addSeparator()

        tb.addAction(st.standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward),
                     "Oldest", lambda: self.set_time_index(0))
        self.act_prev = tb.addAction(st.standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward),
                                     "Previous scan  [", lambda: self.step_time(-1))
        self.act_play = tb.addAction(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
                                     "Play / pause loop  (Space)", self.toggle_play)
        self.act_next = tb.addAction(st.standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward),
                                     "Next scan  ]", lambda: self.step_time(1))
        tb.addAction(st.standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward),
                     "Latest", self.goto_latest)
        self.act_prev.setShortcut(QKeySequence("["))
        self.act_next.setShortcut(QKeySequence("]"))
        self.act_play.setShortcut(QKeySequence(Qt.Key.Key_Space))

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(200)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider.valueChanged.connect(self._slider_changed)
        tb.addWidget(self.slider)
        self.time_label = QLabel("  --  ")
        self.time_label.setMinimumWidth(190)
        tb.addWidget(self.time_label)
        tb.addSeparator()
        tb.addWidget(QLabel(" Loop "))
        self.loop_frames = QSpinBox()
        self.loop_frames.setRange(3, 288)
        self.loop_frames.setValue(24)
        self.loop_frames.setSuffix(" scans")
        self.loop_frames.setToolTip("Number of most recent scans in the animation loop")
        tb.addWidget(self.loop_frames)
        tb.addWidget(QLabel(" Speed "))
        self.speed = QComboBox()
        for label, ms in (("Slow", 600), ("Normal", 300), ("Fast", 150), ("Very fast", 70)):
            self.speed.addItem(label, ms)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(
            lambda _i: self.anim_timer.setInterval(self.speed.currentData()))
        tb.addWidget(self.speed)

        sb = self.statusBar()
        self.st_pos = QLabel()
        self.st_val = QLabel()
        self.st_net = QLabel()
        self.st_pos.setMinimumWidth(170)
        self.st_val.setMinimumWidth(380)
        sb.addWidget(self.st_pos)
        sb.addWidget(self.st_val, 1)
        sb.addPermanentWidget(self.st_net)

        n = self.nav
        n.sites.currentItemChanged.connect(self._site_item_changed)
        n.products.currentRowChanged.connect(self._product_changed)
        n.elevations.currentRowChanged.connect(self._elev_changed)
        n.times.currentRowChanged.connect(self._time_row_changed)
        n.basemap.currentTextChanged.connect(self._basemap_changed)
        n.opacity.valueChanged.connect(self._display_changed)
        n.threshold.valueChanged.connect(lambda _v: self.redraw())
        for cb in (n.rings, n.labels, n.legend, n.smooth):
            cb.toggled.connect(self._display_changed)
        self.map.hovered.connect(self._on_hover)
        self.map.left.connect(lambda: (self.st_pos.clear(), self.st_val.clear()))
        self.map.siteClicked.connect(self.select_site)

    def _build_menus(self):
        mb = self.menuBar()

        m = mb.addMenu("&File")
        a = m.addAction("&Open Radar File (ODIM HDF5 / NEXRAD Level II)...", self.open_local_dialog)
        a.setShortcut(QKeySequence.StandardKey.Open)
        a = m.addAction("&Save Map Image...", self.save_image)
        a.setShortcut(QKeySequence.StandardKey.Save)
        m.addAction("Save Current &Data File...", self.save_data_file)
        m.addSeparator()
        a = m.addAction("E&xit", self.close)
        a.setShortcut(QKeySequence.StandardKey.Quit)

        m = mb.addMenu("&View")
        m.addAction(self.act_nav)
        self.act_toolbar = self.timeline_bar.toggleViewAction()
        self.act_toolbar.setText("Timeline Toolbar")
        m.addAction(self.act_toolbar)
        m.addSeparator()
        bm = m.addMenu("&Basemap")
        self.basemap_group = QActionGroup(self)
        for b in BASEMAPS:
            a = bm.addAction(b.name)
            a.setCheckable(True)
            a.setData(b.name)
            self.basemap_group.addAction(a)
        self.basemap_group.triggered.connect(lambda a: self.nav.basemap.setCurrentText(a.data()))
        self._mirror_check(m, "Range &Rings", self.nav.rings, "R")
        self._mirror_check(m, "Map &Labels on Top", self.nav.labels, "L")
        self._mirror_check(m, "Le&gend", self.nav.legend, "G")
        self.act_sites = QAction("&Site Markers", self, checkable=True, checked=True)
        self.act_sites.toggled.connect(lambda on: (setattr(self.map, "show_sites", on), self.map.update()))
        m.addAction(self.act_sites)
        m.addSeparator()
        a = m.addAction("Zoom to &Radar Range", lambda: self.map.fit_range(
            self.site, self.map.max_range or self.provider.default_range))
        a.setShortcut(QKeySequence("Ctrl+0"))
        a = m.addAction("Zoom to S&witzerland", lambda: self.map.center_on(8.23, 46.80, 7.25))
        a.setShortcut(QKeySequence("Ctrl+9"))
        a = m.addAction("Zoom to &USA", lambda: self.map.center_on(-97.0, 38.5, 4.0))
        a.setShortcut(QKeySequence("Ctrl+8"))
        a = m.addAction("Zoom &In", lambda: self.map.zoom_at(0.5))
        a.setShortcut(QKeySequence.StandardKey.ZoomIn)
        a = m.addAction("Zoom &Out", lambda: self.map.zoom_at(-0.5))
        a.setShortcut(QKeySequence.StandardKey.ZoomOut)

        m = mb.addMenu("&Radar")
        a = m.addAction("&Find Site...", self._focus_filter)
        a.setShortcut(QKeySequence.StandardKey.Find)
        sm = m.addMenu("&Site")
        self.site_group = QActionGroup(self)
        ch = sm.addMenu(NETWORK_NAMES["CH"])
        for i, s in enumerate(CH_SITES):
            a = self._site_action(ch, s)
            a.setShortcut(QKeySequence("Ctrl+%d" % (i + 1)))
        us = sm.addMenu(NETWORK_NAMES["US"])
        regions = OrderedDict()
        for s in sorted(US_SITES, key=lambda s: (US_REGIONS.get(s.region, s.region), s.name)):
            regions.setdefault(s.region, []).append(s)
        for region, sites in regions.items():
            rm = us.addMenu("%s (%s)" % (US_REGIONS.get(region, region), region))
            for s in sites:
                self._site_action(rm, s)
        self.site_group.triggered.connect(lambda a: self.select_site(a.data()))
        self.product_menu = m.addMenu("&Product")
        self.product_group = QActionGroup(self)
        self.product_group.triggered.connect(lambda a: self._set_product(a.data()))
        m.addSeparator()
        a = m.addAction("&Refresh Scan List", lambda: self.load_listing(full=True))
        a.setShortcut(QKeySequence.StandardKey.Refresh)
        self.act_auto = QAction("&Auto Refresh (2 min)", self, checkable=True, checked=True)
        self.act_auto.toggled.connect(lambda on: self.refresh_timer.start(REFRESH_MS) if on
                                      else self.refresh_timer.stop())
        m.addAction(self.act_auto)
        dm = m.addMenu("&MeteoSwiss Data Source")
        self.source_group = QActionGroup(self)
        options = [(-1, "Automatic (primary, then backup)")] + \
                  [(i, lbl) for i, (lbl, _u) in enumerate(PROVIDERS["CH"].sources)]
        for i, label in options:
            a = dm.addAction(label)
            a.setCheckable(True)
            a.setData(i)
            a.setChecked(i == -1)
            self.source_group.addAction(a)
        self.source_group.triggered.connect(self._source_changed)
        m.addSeparator()
        m.addAction("Clear &Download Cache", self.clear_cache)

        m = mb.addMenu("&Help")
        m.addAction("&Help...", self.show_help).setShortcut(QKeySequence.StandardKey.HelpContents)
        m.addAction("&About %s..." % APP_NAME, lambda: AboutDialog(self).exec())

    def _site_action(self, menu, s):
        a = menu.addAction("%s  (%s)" % (s.name, s.code))
        a.setCheckable(True)
        a.setData(s.code)
        self.site_group.addAction(a)
        return a

    def _mirror_check(self, menu, text, checkbox, key):
        a = QAction(text, self, checkable=True)
        a.setChecked(checkbox.isChecked())
        a.setShortcut(QKeySequence("Ctrl+Shift+%s" % key))
        a.toggled.connect(checkbox.setChecked)
        checkbox.toggled.connect(a.setChecked)
        menu.addAction(a)
        return a

    def _focus_filter(self):
        if not self.act_nav.isChecked():
            self.act_nav.setChecked(True)
        self.nav.filter.setFocus()
        self.nav.filter.selectAll()

    def _set_product_choices(self, keys):
        """Fill the product list + menu for the current network / file."""
        keys = [k for k in [p.key for p in PRODUCTS] if k in keys]
        if self.product.key not in keys and keys:
            self.product = PRODUCT_BY_KEY[keys[0]]
        self.nav.set_products(keys, self.product.key)
        self.product_menu.clear()
        for a in self.product_group.actions():
            self.product_group.removeAction(a)
        for i, k in enumerate(keys):
            prod = PRODUCT_BY_KEY[k]
            a = self.product_menu.addAction("%s  (%s)" % (prod.name, prod.key))
            a.setCheckable(True)
            a.setData(k)
            a.setChecked(k == self.product.key)
            if i < 9:
                a.setShortcut(QKeySequence("Alt+%d" % (i + 1)))
            self.product_group.addAction(a)
        self.nav.threshold.setEnabled(self.product.kind == "refl")

    # ---- settings -----------------------------------------------------
    def _restore_settings(self):
        s = self.settings
        geo = s.value("geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        else:
            self.resize(1400, 900)
        split = s.value("splitter")
        if isinstance(split, QByteArray):
            self.splitter.restoreState(split)
        self.site = SITE_BY_CODE.get(s.value("site", CH_SITES[0].code), CH_SITES[0])
        self.product = PRODUCT_BY_KEY.get(s.value("product", "DBZH"), PRODUCTS[0])
        try:
            self.elev = float(s.value("elev")) if s.value("elev") not in (None, "") else None
        except (TypeError, ValueError):
            self.elev = None
        n = self.nav
        n.basemap.setCurrentText(s.value("basemap", BASEMAPS[0].name))
        self._basemap_changed(n.basemap.currentText())
        n.opacity.setValue(int(s.value("opacity", 85)))
        n.threshold.setValue(int(s.value("threshold", 5)))

        def to_bool(v, d):
            return d if v is None else str(v).lower() in ("1", "true")
        n.rings.setChecked(to_bool(s.value("rings"), True))
        n.labels.setChecked(to_bool(s.value("labels"), True))
        n.legend.setChecked(to_bool(s.value("legend"), True))
        n.smooth.setChecked(to_bool(s.value("smooth"), False))
        n.follow_latest.setChecked(to_bool(s.value("follow"), True))
        self.loop_frames.setValue(int(s.value("loop", 24)))
        self.speed.setCurrentIndex(int(s.value("speed", 1)))
        nav_visible = to_bool(s.value("nav_visible"), True)
        self.act_nav.setChecked(nav_visible)
        self.set_nav_visible(nav_visible)
        try:
            self.map.center_on(float(s.value("lon", 8.23)), float(s.value("lat", 46.8)),
                               float(s.value("zoom", 7.25)))
        except (TypeError, ValueError):
            pass
        self.nav.select_site(self.site.code)
        self._check_action(self.site_group, self.site.code)
        self._set_product_choices(self.provider.product_keys)
        self._display_changed()

    def closeEvent(self, ev):
        self.anim_timer.stop()
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("splitter", self.splitter.saveState())
        s.setValue("site", self.site.code)
        s.setValue("product", self.product.key)
        s.setValue("elev", "" if self.elev is None else self.elev)
        n = self.nav
        s.setValue("basemap", n.basemap.currentText())
        s.setValue("opacity", n.opacity.value())
        s.setValue("threshold", n.threshold.value())
        s.setValue("rings", n.rings.isChecked())
        s.setValue("labels", n.labels.isChecked())
        s.setValue("legend", n.legend.isChecked())
        s.setValue("smooth", n.smooth.isChecked())
        s.setValue("follow", n.follow_latest.isChecked())
        s.setValue("loop", self.loop_frames.value())
        s.setValue("speed", self.speed.currentIndex())
        s.setValue("nav_visible", self.act_nav.isChecked())
        lon, lat = norm_to_lonlat(self.map.cx, self.map.cy)
        s.setValue("lon", lon)
        s.setValue("lat", lat)
        s.setValue("zoom", self.map.zoom)
        self.runner.shutdown()
        super().closeEvent(ev)

    @staticmethod
    def _check_action(group, data):
        for a in group.actions():
            if a.data() == data:
                a.setChecked(True)

    # ---- navigation panel visibility ----------------------------------
    def set_nav_visible(self, on):
        sizes = self.splitter.sizes()
        if on:
            if sizes[0] == 0:
                w = max(240, int(self.settings.value("nav_width", 280)))
                self.splitter.setSizes([w, max(200, sum(sizes) - w)])
            self.nav.show()
        else:
            if sizes[0] > 0:
                self.settings.setValue("nav_width", sizes[0])
            self.nav.hide()

    def _sync_nav_action(self, *_):
        visible = self.splitter.sizes()[0] > 0
        if visible != self.act_nav.isChecked():
            self.act_nav.blockSignals(True)
            self.act_nav.setChecked(visible)
            self.act_nav.blockSignals(False)

    # ---- network helpers ----------------------------------------------
    def _get(self, url, callback, timeout=60000):
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", USER_AGENT.encode())
        req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                         QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        req.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        req.setTransferTimeout(timeout)
        reply = self.nam.get(req)

        def done():
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            data = bytes(reply.readAll()) if ok else None
            err = None if ok else reply.errorString()
            reply.deleteLater()
            callback(data, err)
        reply.finished.connect(done)
        return reply

    def _base_url(self, network=None):
        net = network or self.site.network
        prov = PROVIDERS[net]
        return prov.sources[self.source_idx.get(net, 0)][1]

    # ---- scan listing -------------------------------------------------
    def _day_prefixes(self):
        now = datetime.now(timezone.utc)
        return self.provider.prefixes(self.site, [now - timedelta(days=1), now])

    def load_listing(self, full=True):
        """List the last 24 h of scans for the current site."""
        if self.local is not None:
            return
        self._listing_gen += 1
        gen = self._listing_gen
        net = self.site.network
        if full:
            self.entries = {}
            if net == "CH":
                self.source_idx[net] = 0 if self.ch_source_mode < 0 else self.ch_source_mode
            else:
                self.source_idx[net] = 0
        self._list_prefixes(gen, self._day_prefixes(), [], full)

    def refresh_listing(self):
        if self.local is None:
            self.load_listing(full=not self.entries)

    def _list_prefixes(self, gen, prefixes, found, full):
        if gen != self._listing_gen:
            return
        if not prefixes:
            self._listing_done(gen, found, full)
            return
        prefix = prefixes[0]
        start_after = ""
        if not full:
            keys = [e.key for lst in self.entries.values() for e in lst if e.key.startswith(prefix)]
            start_after = max(keys) if keys else ""
        self._list_page(gen, prefix, start_after, "", prefixes, found, full)

    def _list_page(self, gen, prefix, start_after, token, prefixes, found, full):
        url = "%s/?list-type=2&prefix=%s" % (self._base_url(), quote(prefix, safe=""))
        if start_after:
            url += "&start-after=%s" % quote(start_after, safe="")
        if token:
            url += "&continuation-token=%s" % quote(token, safe="")
        self.st_net.setText("Listing %s ..." % self.site.code)
        provider = self.provider

        def done(data, err):
            if gen != self._listing_gen:
                return
            if err:
                self.st_net.setText("Listing failed: %s" % err)
                self._listing_done(gen, found, full, error=err)
                return
            try:
                root = ET.fromstring(data)
            except ET.ParseError as exc:
                self._listing_done(gen, found, full, error=str(exc))
                return
            for c in root.iter(S3_NS + "Contents"):
                e = provider.parse_key(c.findtext(S3_NS + "Key") or "")
                if e is not None:
                    found.append(e)
            if (root.findtext(S3_NS + "IsTruncated") or "").lower() == "true":
                nxt = root.findtext(S3_NS + "NextContinuationToken") or ""
                self._list_page(gen, prefix, start_after, nxt, prefixes, found, full)
            else:
                self._list_prefixes(gen, prefixes[1:], found, full)
        self._get(url, done, timeout=30000)

    def _listing_done(self, gen, found, full, error=None):
        if gen != self._listing_gen:
            return
        net = self.site.network
        prov = self.provider
        idx = self.source_idx.get(net, 0)
        if (full and not found and net == "CH" and self.ch_source_mode < 0
                and idx + 1 < len(prov.sources)):
            self.source_idx[net] = idx + 1     # automatic: fall over to the next source
            self._list_prefixes(gen, self._day_prefixes(), [], full)
            return
        cutoff = datetime.now(timezone.utc) - HISTORY
        for e in found:
            self.entries.setdefault(e.elev, []).append(e)
        for el in list(self.entries):
            lst = sorted({e.key: e for e in self.entries[el] if e.when >= cutoff}.values(),
                         key=lambda e: e.when)
            if lst:
                self.entries[el] = lst
            else:
                del self.entries[el]
        total = sum(len(v) for v in self.entries.values())
        src = prov.sources[self.source_idx.get(net, 0)][0]
        if total == 0:
            msg = "No scans found for %s (%s)" % (self.site.name, src)
            if error:
                msg += "\n" + error
            self.st_net.setText(msg.split("\n")[0])
            self.map.clear_radar(msg)
        else:
            note = "  |  %s" % prov.lag_note if prov.lag_note else ""
            self.st_net.setText("%d scans  |  %s  |  updated %s UTC%s"
                                % (total, src, datetime.now(timezone.utc).strftime("%H:%M"), note))
        if prov.per_elevation_files:
            self._populate_elevations(sorted(k for k in self.entries if k is not None))
        self._populate_times()

    # ---- list population ----------------------------------------------
    def _per_elevation(self):
        return self.local is None and self.provider.per_elevation_files

    def _populate_elevations(self, elevs):
        """Fill the elevation list; keeps the selection (nearest match)."""
        lw = self.nav.elevations
        current = [lw.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lw.count())]
        if elevs != current:
            lw.blockSignals(True)
            lw.clear()
            for el in elevs:
                it = QListWidgetItem("%5.1f°" % el)
                it.setData(Qt.ItemDataRole.UserRole, el)
                lw.addItem(it)
            lw.blockSignals(False)
        if not elevs:
            return
        if self.elev is None or self.elev not in elevs:
            if self.elev is None:
                pos = [e for e in elevs if e >= 0]
                self.elev = pos[0] if pos else elevs[0]
            else:
                self.elev = min(elevs, key=lambda e: abs(e - self.elev))
        lw.blockSignals(True)
        lw.setCurrentRow(elevs.index(self.elev))
        lw.blockSignals(False)

    def _populate_times(self, keep_when=None):
        if self.local is not None:
            self.times = [self.local[1]]
        elif self._per_elevation():
            self.times = list(self.entries.get(self.elev, []))
        else:
            self.times = list(self.entries.get(None, []))
        lw = self.nav.times
        lw.blockSignals(True)
        lw.clear()
        fmt = "%Y-%m-%d  %H:%M" if self._per_elevation() else "%Y-%m-%d  %H:%M:%S"
        for e in reversed(self.times):     # newest first
            lw.addItem(e.when.strftime(fmt))
        lw.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.times) - 1))
        self.slider.blockSignals(False)
        if not self.times:
            self.time_idx = -1
            self.time_label.setText("  --  ")
            return
        if self.nav.follow_latest.isChecked() or (keep_when is None and self.time_idx < 0):
            idx = len(self.times) - 1
        else:
            target = keep_when or (self.current.start if self.current else self.times[-1].when)
            idx = min(range(len(self.times)),
                      key=lambda i: abs((self.times[i].when - target).total_seconds()))
        self.set_time_index(idx, force=True)

    # ---- selection handlers -------------------------------------------
    def _site_item_changed(self, cur, _prev):
        if cur is None:
            return
        code = cur.data(0, Qt.ItemDataRole.UserRole)
        if code:
            self.select_site(code)

    def select_site(self, code, force=False):
        site = SITE_BY_CODE.get(code)
        if site is None:
            return
        changed = force or site is not self.site or self.local is not None or not self.entries
        net_changed = site.network != self.site.network or self.local is not None
        self.site = site
        self.local = None
        self.nav.select_site(code)
        self._check_action(self.site_group, code)
        prov = self.provider
        self.map.set_site(site, prov.default_range)
        if not self.map.is_visible(site.lon, site.lat):
            self.map.fit_range(site, prov.default_range)
        if not changed:
            return
        self.stop_play()
        if net_changed:
            self.elev = None
            self.nav.elevations.clear()
            self._set_product_choices(prov.product_keys)
        elif not prov.per_elevation_files:
            pass  # keep the elevation; each NEXRAD volume maps it to its nearest cut
        self.current = None
        self.time_idx = -1
        self.times = []
        self.map.clear_radar("Loading %s ..." % site.name)
        self.load_listing(full=True)

    def _product_changed(self, row):
        if row >= 0:
            self._set_product(self.nav.products.item(row).data(Qt.ItemDataRole.UserRole))

    def _set_product(self, key):
        self.product = PRODUCT_BY_KEY.get(key, PRODUCTS[0])
        self.nav.select_product(self.product.key)
        self._check_action(self.product_group, self.product.key)
        self.nav.threshold.setEnabled(self.product.kind == "refl")
        self.redraw()

    def _elev_changed(self, row):
        if row < 0:
            return
        self.elev = self.nav.elevations.item(row).data(Qt.ItemDataRole.UserRole)
        if self._per_elevation():
            keep = self.times[self.time_idx].when if 0 <= self.time_idx < len(self.times) else None
            self._populate_times(keep_when=None if self.nav.follow_latest.isChecked() else keep)
        else:
            self.redraw()

    def _time_row_changed(self, row):
        if row < 0:
            return
        idx = len(self.times) - 1 - row
        self.nav.follow_latest.setChecked(idx == len(self.times) - 1)
        self.set_time_index(idx)

    def _slider_changed(self, v):
        self.nav.follow_latest.setChecked(v == len(self.times) - 1)
        self.set_time_index(v)

    def goto_latest(self):
        self.nav.follow_latest.setChecked(True)
        self.set_time_index(len(self.times) - 1)

    def set_time_index(self, idx, force=False):
        if not self.times:
            return
        idx = max(0, min(len(self.times) - 1, idx))
        if idx == self.time_idx and not force:
            return
        self.time_idx = idx
        e = self.times[idx]
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        lw = self.nav.times
        lw.blockSignals(True)
        lw.setCurrentRow(len(self.times) - 1 - idx)
        lw.blockSignals(False)
        age = datetime.now(timezone.utc) - e.when
        self.time_label.setText("  %s UTC  (%s ago)" % (e.when.strftime("%d %b %H:%M"),
                                                        _fmt_age(age)))
        self._show_entry(e)

    def step_time(self, delta):
        if self.times:
            self.nav.follow_latest.setChecked(False)
            self.set_time_index((self.time_idx + delta) % len(self.times))

    # ---- data pipeline: file -> Volume -> Sweep -> Frame ----------------
    def _disk_path(self, key, network):
        d = self.data_dir / network
        d.mkdir(exist_ok=True)
        return d / key.rsplit("/", 1)[-1]

    def _request_volume(self, entry: ScanEntry, network, callback):
        """Deliver the decoded Volume for an entry via callback(volume, error)."""
        if self.local is not None and entry is self.local[1]:
            callback(self.local[0], None)
            return
        key = entry.key
        vol = self._vol_cache.get(key)
        if vol is not None:
            self._vol_cache.move_to_end(key)
            callback(vol, None)
            return
        waiters = self._vol_waiters.get(key)
        if waiters is not None:
            waiters.append(callback)
            return
        self._vol_waiters[key] = [callback]
        prov = PROVIDERS[network]
        path = self._disk_path(key, network)

        def decoded(vol_, err):
            if vol_ is not None:
                self._vol_cache[key] = vol_
                self._meta[key] = (vol_.elevations(), vol_.products())
                while len(self._vol_cache) > 4:       # NEXRAD volumes hold ~50 MB each
                    self._vol_cache.popitem(last=False)
            elif path.is_file():
                path.unlink(missing_ok=True)          # corrupt cache file
            for cb in self._vol_waiters.pop(key, []):
                cb(vol_, err)

        if path.is_file():
            self.runner.submit(lambda: prov.decode(path.read_bytes(), key), decoded)
            return

        def downloaded(data, err):
            if data is None:
                for cb in self._vol_waiters.pop(key, []):
                    cb(None, err or "download failed")
                return

            def work():
                path.write_bytes(data)
                return prov.decode(data, key)
            self.runner.submit(work, decoded)
            self._downloads += 1
            if self._downloads % 25 == 0:
                self._prune_disk_cache()
        self._get("%s/%s" % (self._base_url(network), quote(key)), downloaded)

    def _elev_for(self, entry):
        return entry.elev if entry.elev is not None else self.elev

    def _threshold(self):
        return float(self.nav.threshold.value()) if self.product.kind == "refl" else 0.0

    def _frame_key(self, entry):
        return (entry.key, self._elev_for(entry), self.product.key, self._threshold())

    def _request_frame(self, entry: ScanEntry, callback):
        """Deliver a rendered Frame for entry at the current elevation/product."""
        elev, prod, thr = self._elev_for(entry), self.product, self._threshold()
        fk = (entry.key, elev, prod.key, thr)
        hit = self._img_cache.get(fk)
        if hit is not None:
            self._img_cache.move_to_end(fk)
            callback(hit, None)
            return
        waiters = self._frame_waiters.get(fk)
        if waiters is not None:
            waiters.append(callback)
            return
        self._frame_waiters[fk] = [callback]
        sk = (entry.key, elev, prod.key)
        network = self.site.network

        def finish(frame, err):
            if frame is not None:
                self._img_cache[fk] = frame
                while len(self._img_cache) > 60:
                    self._img_cache.popitem(last=False)
                self._sweep_cache[sk] = frame.sweep
                while len(self._sweep_cache) > 80:
                    self._sweep_cache.popitem(last=False)
            for cb in self._frame_waiters.pop(fk, []):
                cb(frame, err)

        def render(sw, meta):
            img, bounds = render_sweep(sw, prod, thr)
            return Frame(sw, img, bounds, meta[0], meta[1])

        cached = self._sweep_cache.get(sk)
        if cached is not None and entry.key in self._meta:
            meta = self._meta[entry.key]
            self.runner.submit(lambda: render(cached, meta), finish)
            return

        def with_volume(vol, err):
            if vol is None:
                finish(None, err)
                return
            meta = (vol.elevations(), vol.products())

            def work():
                sw = vol.sweep(elev, prod.key)
                if sw is None:
                    raise LookupError("%s is not in this scan (has %s)"
                                      % (prod.key, ", ".join(sorted(vol.products())) or "nothing"))
                return render(sw, meta)
            self.runner.submit(work, finish)
        self._request_volume(entry, network, with_volume)

    def _show_entry(self, e: ScanEntry):
        fk = self._frame_key(e)

        def got(frame, err):
            cur = self.times[self.time_idx] if 0 <= self.time_idx < len(self.times) else None
            if cur is None or self._frame_key(cur) != fk:
                return  # user moved on
            if frame is None:
                self.map.clear_radar("Cannot show scan\n%s" % (err or "unknown error"))
                return
            self._display_frame(frame)
        self._request_frame(e, got)

    def _display_frame(self, frame: Frame):
        sw = frame.sweep
        self.current = sw
        if not self._per_elevation():
            self._populate_elevations(frame.elevations)
        if frame.products and self.local is not None:
            self._set_product_choices(frame.products)
        site = SITE_BY_CODE.get(sw.site_code) or SITE_BY_CODE.get(sw.site_code.upper())
        name = site.name if site else sw.site_code
        self.map.threshold = self._threshold()
        self.map.set_site(site or RadarSite(sw.site_code, name, sw.lat, sw.lon, sw.height,
                                            network=self.site.network),
                          float(ground_range(sw.max_range, sw.elangle)))
        info = "%s %s  %s  %.1f°\n%s UTC" % (
            sw.site_code.upper() if self.site.network == "US" else "", name,
            self.product.key, sw.elangle, sw.start.strftime("%Y-%m-%d %H:%M:%S"))
        self.map.set_radar(frame.image, frame.bounds, self.product,
                           sw.nyquist or 30.0, info.strip())

    def redraw(self):
        if 0 <= self.time_idx < len(self.times):
            self._show_entry(self.times[self.time_idx])

    def _display_changed(self, *_):
        n, m = self.nav, self.map
        m.opacity = n.opacity.value() / 100.0
        m.show_rings = n.rings.isChecked()
        m.show_labels = n.labels.isChecked()
        m.show_legend = n.legend.isChecked()
        m.smooth = n.smooth.isChecked()
        m.update()

    def _basemap_changed(self, name):
        self.map.set_basemap(name)
        if hasattr(self, "basemap_group"):
            self._check_action(self.basemap_group, name)

    def _source_changed(self, action):
        self.ch_source_mode = action.data()
        if self.site.network == "CH":
            self.load_listing(full=True)

    # ---- hover readout ------------------------------------------------
    def _on_hover(self, lon, lat):
        self.st_pos.setText("%.4f°  %.4f°" % (lat, lon))
        sw = self.current
        if sw is None:
            self.st_val.clear()
            return
        dist, az = dist_bearing(sw.lat, sw.lon, lat, lon)
        dist, az = float(dist), float(az)
        r = dist   # invert ground range -> slant range (monotonic, few Newton steps)
        for _ in range(4):
            r += dist - float(ground_range(r, sw.elangle))
        b = int((r - sw.rstart) // sw.rscale)
        txt = "Range %.1f km  Az %.1f°" % (dist / 1000.0, az)
        if 0 <= b < sw.nbins and r <= sw.max_range:
            ray = int(az // (360.0 / sw.nrays)) % sw.nrays
            hgt = (float(beam_height(r, sw.elangle)) + sw.height) / 1000.0
            txt += "  Beam %.2f km MSL" % hgt
            q = sw.quantities.get(self.product.key)
            if q is not None:
                v = q.value_at(ray, b)
                unit = " " + self.product.unit if self.product.unit else ""
                txt += "  |  %s %s" % (self.product.key,
                                       "--" if v is None else ("%.2f" % v if abs(v) < 2 else "%.1f" % v) + unit)
        else:
            txt += "  (out of range)"
        self.st_val.setText(txt)

    # ---- animation ----------------------------------------------------
    def toggle_play(self):
        if self.anim_timer.isActive():
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        if len(self.times) < 2:
            return
        self.nav.follow_latest.setChecked(False)
        n = min(self.loop_frames.value(), len(self.times))
        self._loop_start = len(self.times) - n
        for e in self.times[self._loop_start:]:         # prefetch the loop
            self._request_frame(e, lambda *_: None)
        if self.time_idx < self._loop_start:
            self.set_time_index(self._loop_start)
        self.anim_timer.start(self.speed.currentData())
        self.act_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))

    def stop_play(self):
        self.anim_timer.stop()
        self.act_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _anim_step(self):
        if len(self.times) < 2:
            self.stop_play()
            return
        start = max(0, min(getattr(self, "_loop_start", 0), len(self.times) - 2))
        nxt = self.time_idx + 1
        if nxt >= len(self.times) or nxt < start:
            nxt = start
        e = self.times[nxt]
        if self._frame_key(e) not in self._img_cache:
            self._request_frame(e, lambda *_: None)
            # skip frames that failed to load rather than stalling the loop
            if self._frame_key(e) in self._frame_waiters:
                return
        self.set_time_index(nxt)
        hold = 4 if nxt == len(self.times) - 1 else 1   # pause on the newest frame
        self.anim_timer.setInterval(self.speed.currentData() * hold)

    # ---- file actions -------------------------------------------------
    def open_local_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open radar file", str(Path.home()),
            "Radar files (*.h5 *.hdf *.hdf5 *_V0* *.gz *.ar2 *.ar2v);;All files (*)")
        if path:
            self.open_local(path)

    def open_local(self, path):
        try:
            vol = decode_file(Path(path).read_bytes(), str(path))
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, "Cannot read %s:\n%s" % (path, exc))
            return
        if not vol.elevations():
            QMessageBox.warning(self, APP_NAME, "No sweeps found in %s" % path)
            return
        self.stop_play()
        any_sweep = vol.sweep(None, sorted(vol.products())[0])
        entry = ScanEntry(any_sweep.start, None, "local:%s" % path)
        self.local = (vol, entry)
        site = SITE_BY_CODE.get(vol.site_code) or SITE_BY_CODE.get(vol.site_code.upper())
        if site:
            self.site = site
            self.nav.select_site(site.code)
            self._check_action(self.site_group, site.code)
        self.map.center_on(any_sweep.lon, any_sweep.lat)
        self.elev = None
        self.nav.elevations.clear()
        self.time_idx = -1
        self._set_product_choices(vol.products())
        self.st_net.setText("Local file: %s  (%d elevations)" % (Path(path).name, len(vol.elevations())))
        self._populate_times()

    def save_image(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save map image", str(Path.home() / "radar.png"), "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self.map.grab().save(path)
            self.statusBar().showMessage("Saved %s" % path, 5000)

    def save_data_file(self):
        if self.local is not None or not (0 <= self.time_idx < len(self.times)):
            return
        key = self.times[self.time_idx].key
        src = self._disk_path(key, self.site.network)
        if not src.is_file():
            return
        name = src.name
        path, _ = QFileDialog.getSaveFileName(self, "Save radar data file", str(Path.home() / name))
        if path:
            Path(path).write_bytes(src.read_bytes())
            self.statusBar().showMessage("Saved %s" % path, 5000)

    def clear_cache(self):
        n = 0
        for f in self.data_dir.rglob("*"):
            if f.is_file():
                f.unlink(missing_ok=True)
                n += 1
        self._vol_cache.clear()
        self._sweep_cache.clear()
        self._img_cache.clear()
        cache = self.nam.cache()
        if cache is not None:
            cache.clear()
        self.statusBar().showMessage("Removed %d cached data files and the tile cache" % n, 5000)

    def _prune_disk_cache(self, max_bytes=2 * 1024 ** 3, max_age=2 * 86400):
        """Drop data files older than 2 days, then the oldest beyond 2 GB."""
        files = []
        cutoff = time.time() - max_age
        for f in self.data_dir.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                if st.st_mtime < cutoff:
                    f.unlink()
                else:
                    files.append((st.st_mtime, st.st_size, f))
            except OSError:
                pass
        total = sum(s for _m, s, _f in files)
        for _m, size, f in sorted(files):
            if total <= max_bytes:
                break
            try:
                f.unlink()
                total -= size
            except OSError:
                pass

    def show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("%s Help" % APP_NAME)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(_HELP_HTML)
        box.exec()


def _fmt_age(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    if mins < 60:
        return "%d min" % max(0, mins)
    return "%dh %02dm" % (mins // 60, mins % 60)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RadarViewer")
    app.setOrganizationName(ORG_NAME)
    app.setWindowIcon(app_icon())
    path = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else None
    win = MainWindow(path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
