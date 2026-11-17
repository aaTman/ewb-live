"""GPM IMERG precipitation ingest for the ``flood`` event type.

Owner spec (2026-11-16): "use ERA5 for climatologies for QPF, then use IMERG/GPM for the
past 90 days of observations". This module owns the **observation** half; the ERA5
climatology is role ``flood-clim``'s half, and ``flood-detect`` joins the two. Product
choice, latency/access evidence and the storage layout: ``notes/flood_design.md`` (Part 1).

Product / run policy
    ``GPM_3IMERGDL`` **Late Run** daily is the run of record (the owner's preference: it
    uses forward *and* backward morphing), with ``GPM_3IMERGDE`` **Early Run** daily filling
    the ~15 h head the Late Run has not reached — ``product="auto"``, the default. Pin one
    run with ``product="early"|"late"|"final"`` (Final lags ~3.5 months, so it cannot cover a
    rolling window). Granules are one per UTC day, ~26 MB each:
    ``3B-DAY-{E,L}.MS.MRG.3IMERG.<YYYYMMDD>-S000000-E235959.V07C.nc4``. Measured latencies
    (CMR ``ProviderDates``, re-checked 2026-11-17): Early daily D+1 04:29Z, Late daily
    D+1 14:29Z, and both runs published through D-2 at the time of writing, so ``auto``
    rarely pays for the Early run. Final daily has no granules for the recent window at all
    (0 CMR hits for 2026-11-04..11-18), as its ~3.5-month latency implies — so the daily
    products are the ones that can carry a rolling 90-day window.

Grid / units
    0.1° cell-centred global grid exactly as carried by the granule (1800 lat −89.95…89.95
    ascending, 3600 lon −180…179.95); coordinates are read per file and asserted (0.1°
    spacing, global coverage) instead of hardcoded. Output: **(time, lat, lon) in mm/day**,
    ``time`` = 00:00 UTC of the UTC calendar day (00:00:00–23:59:59.999 — the granule's own
    ``S000000-E235959`` window; the half-hourly route buckets by the step's UTC date).

Never a silent zero-fill
    producer fill values (negative, e.g. −9999) and non-finite cells become NaN; a day with
    no published granule stays an all-NaN day flagged ``missing`` in
    :func:`load_imerg_status`; per-pixel sampling completeness travels with the field as the
    producer's ``precipitation_cnt`` (how many of the 48 half-hourly estimates the daily mean
    used) and is surfaced by :func:`load_imerg_mask`.

Credentials
    NASA Earthdata login, read at runtime from ``~/.netrc`` (machine
    ``urs.earthdata.nasa.gov``) or ``EARTHDATA_USERNAME``/``EARTHDATA_PASSWORD`` — never
    stored in the repo. It is needed by both transports: the AWS Open Data mirror lists
    these buckets as ``ControlledAccess`` (unsigned S3 requests are refused), so its
    temporary credentials come from the Earthdata-backed ``s3credentials`` endpoint. With
    a login in place, ``route="auto"`` prefers same-region S3 and falls back to GES DISC
    HTTPS. Granule discovery via NASA CMR is anonymous, so ``survey``, ``survey_window``
    and ``update --dry-run`` work without credentials.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.cookiejar
import json
import netrc
import os
import random
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

# --------------------------------------------------------------------------- config

IMERG_GRID_DEG = 0.1
UNITS = "mm/day"
HOURS_PER_DAY = 24.0
STEPS_PER_DAY = 48          # half-hourly granules per UTC day
SCHEMA_VERSION = 1

#: mask bits of :func:`load_imerg_mask` (uint8, several bits may be set)
MASK_PRESENT = 1            # a real observation at that cell (not a fill/NaN cell)
MASK_FULL_SAMPLING = 2      # all 48 half-hourly steps contributed (cnt == 48, or unknown)
MASK_LIKELY_LIQUID = 4      # probabilityLiquidPrecipitation == 100 (liquid, or no precip)
MASK_BITS = {MASK_PRESENT: "present", MASK_FULL_SAMPLING: "full_sampling",
             MASK_LIKELY_LIQUID: "likely_liquid"}
CNT_UNKNOWN = 255           # producer did not publish sampling counts

DEFAULT_KEEP_DAYS = 90
DEFAULT_KEEP_RAW = 3
#: ``auto`` = Late Run where it exists (the owner's preferred run), Early Run only for
#: the ~15 h head the Late Run has not reached yet. Pin one run with ``product="early"`` /
#: ``"late"`` / ``"final"`` for a strictly homogeneous series.
DEFAULT_PRODUCT = "auto"
PRODUCT_KEYS = ("auto", "early", "late", "final")
ROUTES = ("auto", "s3", "https")
SCREENS = ("none", "cnt", "polid", "cnt+polid")

#: Refuse to start a fetch that would leave less than this free on the cache filesystem.
#: The ewb LXC root FS runs at 98-100 % (4.5 GB free on 2026-11-17) and hosts the live
#: service, so an ingest that fills the disk is an outage: measure first, then download.
MIN_FREE_BYTES = int(os.environ.get("EWB_LIVE_IMERG_MIN_FREE_BYTES", str(2 << 30)))
#: projection constants (measured: deflate daily field 6-12 MB, raw granule ~26.5 MB)
EST_DAILY_BYTES = 14 << 20
EST_RAW_BYTES = 30 << 20
#: days fetched before ``raw/`` is rolled (one batch of granules is the peak cost)
DEFAULT_BATCH_DAYS = 5

CMR_SEARCH_URL = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
#: AWS Open Data mirror of the same GES DISC collections (registry.opendata.aws
#: /nasa-gpm3imerghhl etc.). NOT anonymous: the bucket is ``ControlledAccess`` and
#: anonymous GET/LIST answer 403 "Anonymous users cannot invoke requests against
#: Requester Pays buckets", so temporary credentials come from the Earthdata-backed
#: s3credentials endpoint below. It is still the preferred transport (same-region S3,
#: no Apache redirect, no requester-pays surprise).
S3_CREDENTIALS_URL = os.environ.get("EWB_LIVE_IMERG_S3_CREDENTIALS",
                                    "https://data.gesdisc.earthdata.nasa.gov/s3credentials")
S3_BUCKET = os.environ.get("EWB_LIVE_IMERG_BUCKET", "gesdisc-cumulus-prod-protected")
S3_REGION = os.environ.get("EWB_LIVE_IMERG_S3_REGION", "us-west-2")
DEFAULT_ROUTE = os.environ.get("EWB_LIVE_IMERG_ROUTE", "auto")
EDL_MACHINE = os.environ.get("EWB_LIVE_EDL_MACHINE", "urs.earthdata.nasa.gov")
#: The Basic-auth header is only ever sent to these hosts (never a wildcard, so a
#: redirect cannot carry the password somewhere unintended).
AUTH_HOSTS = frozenset({
    "urs.earthdata.nasa.gov",
    "data.gesdisc.earthdata.nasa.gov",
    "gpm1.gesdisc.eosdis.nasa.gov",
    "opendap.earthdata.nasa.gov",
})

#: Hard ceiling on one granule body (daily ≈ 26 MB, half-hourly ≈ 1 MB; 512 MB is ~20x
#: the largest thing this endpoint ever serves). A response is copied into a ``.part``
#: file with an explicit EOF check, so an endpoint that never signals end-of-body — or a
#: resume loop that keeps appending — fails fast instead of filling the filesystem. This
#: is what a runaway test (a stub ``read()`` with no EOF) once did: 140 GB into tmpfs.
MAX_GRANULE_BYTES = int(os.environ.get("EWB_LIVE_IMERG_MAX_GRANULE_BYTES", 512 << 20))
MAX_BODY_BYTES = int(os.environ.get("EWB_LIVE_IMERG_MAX_BODY_BYTES", 64 << 20))

REQUEST_MIN_INTERVAL_S = float(os.environ.get("EWB_LIVE_IMERG_REQUEST_INTERVAL_S", "0.5"))
CONNECT_TIMEOUT_S = float(os.environ.get("EWB_LIVE_IMERG_CONNECT_TIMEOUT_S", "20"))
READ_TIMEOUT_S = float(os.environ.get("EWB_LIVE_IMERG_READ_TIMEOUT_S", "180"))
#: connect timeout is applied through the opener's socket timeout (urllib has one knob)
DOWNLOAD_TRIES = int(os.environ.get("EWB_LIVE_IMERG_DOWNLOAD_TRIES", "5"))
MAX_PLAUSIBLE_MM_DAY = 5000.0    # producer range tops out at 50 mm/hr → 1200 mm/day
#: global daily-mean plausibility band — the guard that turns a mislabelled producer
#: ``units`` attribute (a 24x error) into a failed ingest instead of a corrupt record
MEAN_MM_DAY_BOUNDS = (0.05, 25.0)

_HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
_NC_MAGIC = b"CDF"
_DAY_IN_NAME = re.compile(r"(20\d{6})")


@dataclass(frozen=True)
class Product:
    """One GES DISC IMERG daily collection."""

    key: str
    short_name: str
    concept_id: str
    run: str
    latency_h: float              # producer latency, hours after the UTC day closes
    granule_prefix: str

    @property
    def collection_dir(self) -> str:
        """GES DISC collection directory name under ``GPM_L3``."""
        return f"{self.short_name}.07"


PRODUCTS: dict[str, Product] = {
    "early": Product("early", "GPM_3IMERGDE", "C2723754850-GES_DISC", "Early", 4.5,
                     "3B-DAY-E.MS.MRG.3IMERG"),
    "late": Product("late", "GPM_3IMERGDL", "C2723754859-GES_DISC", "Late", 18.0,
                    "3B-DAY-L.MS.MRG.3IMERG"),
    "final": Product("final", "GPM_3IMERGDF", "C2723754864-GES_DISC", "Final", 3.5 * 720,
                     "3B-DAY.MS.MRG.3IMERG"),
}

#: Half-hourly counterparts (reference). :func:`accumulate_half_hourly` consumes the
#: 48-step field; the supported pipeline reads the daily product, because half-hourly
#: ingest of a 90-day window is ~55 GB against ~2.4 GB daily (notes/flood_design.md).
HH_PRODUCTS: dict[str, str] = {"early": "GPM_3IMERGHHE", "late": "GPM_3IMERGHHL",
                              "final": "GPM_3IMERGHH"}


class ImergError(RuntimeError):
    """Unreadable/odd granule (units, grid), or a failed download."""


class ImergAuthError(ImergError):
    """No, or rejected, NASA Earthdata credentials."""


_AUTH_STEPS = (
    "NASA Earthdata credentials are required for IMERG: both the GES DISC endpoint and the "
    "AWS Open Data mirror are Controlled Access (unsigned S3 is refused; the mirror's "
    "temporary credentials come from data.gesdisc.earthdata.nasa.gov/s3credentials).\n"
    "  1. Create a free account: https://urs.earthdata.nasa.gov/users/new\n"
    "  2. On the ewb LXC create /root/.netrc, mode 600, one line:\n"
    "       machine urs.earthdata.nasa.gov login <username> password <password>\n"
    "     (or export EARTHDATA_USERNAME / EARTHDATA_PASSWORD for the service env)\n"
    "  3. Keep them out of the repo — nothing here stores them; they are read at\n"
    "     runtime from the netrc/env only.\n"
    "  4. Verify:  ops/remote.sh run python -m ewb_live.sources.imerg auth-check\n"
)


def earthdata_credentials(machine: str = EDL_MACHINE) -> tuple[str, str] | None:
    """Earthdata user/password from the environment or ``~/.netrc``; ``None`` if absent."""
    user = os.environ.get("EARTHDATA_USERNAME")
    pw = os.environ.get("EARTHDATA_PASSWORD") or os.environ.get("EARTHDATA_PWD")
    if user and pw:
        return user, pw
    path = os.environ.get("NETRC") or str(Path.home() / ".netrc")
    try:
        auth = netrc.netrc(path).authenticators(machine)
    except (FileNotFoundError, netrc.NetrcParseError, OSError):
        return None
    if not auth or not auth[0] or not auth[2]:
        return None
    return auth[0], auth[2]


def imerg_root() -> Path:
    """Root of the IMERG cache (on the ewb LXC: ``/opt/ewb-live/data/imerg``)."""
    return Path(os.environ.get("EWB_LIVE_IMERG_DIR") or (Path("data") / "imerg"))


def product_for(key: str) -> Product:
    """Concrete collection for a run key (``auto`` resolves to its preferred run)."""
    if key == "auto":
        key = "late"
    try:
        return PRODUCTS[key]
    except KeyError:
        raise ImergError(f"unknown IMERG product {key!r}; use one of "
                         f"{'|'.join(PRODUCT_KEYS)}") from None


def run_label(product: str) -> str:
    """Human-readable run description for attrs and CLI output."""
    if product == "auto":
        return "Late Run, Early Run for the head (auto)"
    return f"{product_for(product).run} Run"


# ------------------------------------------------------------------ polite HTTP I/O


class _Gate:
    """Minimum spacing between remote requests (polite use of GES DISC / CMR)."""

    def __init__(self, min_interval: float = REQUEST_MIN_INTERVAL_S) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if sleep_for > 0:
            time.sleep(sleep_for)


_GATE = _Gate()
_RETRY_CODES = (429, 500, 502, 503, 504)


def _http_get(url: str, opener: urllib.request.OpenerDirector | None = None, *,
              timeout: float = READ_TIMEOUT_S, headers: dict[str, str] | None = None,
              tries: int = 3) -> bytes:
    """GET with pacing, retries and exponential backoff on 429/5xx."""
    op = opener or urllib.request.build_opener()
    last: Exception | None = None
    for attempt in range(tries):
        _GATE.wait()
        req = urllib.request.Request(url, headers={"User-Agent": "ewb-live/imerg",
                                                   **(headers or {})})
        try:
            with op.open(req, timeout=timeout) as resp:
                return _read_capped(resp, MAX_BODY_BYTES, url)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in _RETRY_CODES:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        time.sleep(min(60.0, 2.0 ** attempt) + random.uniform(0.0, 0.5))
    raise ImergError(f"GET failed after {tries} tries: {url} ({last})")


@dataclass(frozen=True)
class Granule:
    """One producer granule = one UTC day."""

    day: dt.date
    granule_ur: str
    url: str
    size_mb: float = 0.0
    produced: str = ""
    product: str = "early"

    @property
    def filename(self) -> str:
        return self.granule_ur.rsplit(":", 1)[-1] or Path(self.url).name


def survey(start: dt.date, end: dt.date, *, product: str = DEFAULT_PRODUCT,
           page_size: int = 2000) -> dict[dt.date, Granule]:
    """Published granules for ``[start, end]`` (inclusive) keyed by UTC day.

    Anonymous NASA CMR search (no credentials needed). A day missing from the result has
    no granule — that is a real data gap (⇒ ``missing``), never a zero.
    """
    if end < start:
        return {}
    if product == "auto":
        raise ImergError("'auto' is a run policy, not a collection — use survey_window() "
                         "or name early|late|final")
    prod = product_for(product)
    items: list[dict] = []
    page = 1
    while True:
        params = {
            "short_name": prod.short_name,
            "temporal": f"{start:%Y-%m-%dT%H:%M:%SZ},{end:%Y-%m-%dT%H:%M:%SZ}",
            "page_size": page_size,
            "page_num": page,
            # NOTE: no `sortable` — CMR answers HTTP 400 "Parameter [sortable] was not
            # recognized" for these collections, which failed every survey. The result is
            # keyed by day below, so the order does not matter.
        }
        payload = json.loads(_http_get(CMR_SEARCH_URL + "?" + urllib.parse.urlencode(params),
                                       tries=DOWNLOAD_TRIES))
        batch = payload.get("items", [])
        items.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    out: dict[dt.date, Granule] = {}
    for it in items:
        u = it.get("umm", it)
        begin = ((u.get("TemporalExtent") or {}).get("RangeDateTime") or {}).get(
            "BeginningDateTime") or ""
        try:
            day = dt.date.fromisoformat(begin[:10])
        except ValueError:
            continue
        url = next((r.get("URL", "") for r in u.get("RelatedUrls", [])
                    if r.get("Type") == "GET DATA" and str(r.get("URL")).startswith("https://")),
                   "")
        if not url:
            continue
        archive = ((u.get("DataGranule") or {}).get("ArchiveAndDistributionInformation") or [{}])
        # CMR's umm_json does not populate Size for these granules (measured 2026-11-17),
        # so 0.0 means "unknown" and the disk projection falls back to EST_RAW_BYTES.
        produced = next((p.get("Date", "") for p in u.get("ProviderDates", [])
                         if p.get("Type") == "Update"), "")
        out[day] = Granule(day=day, granule_ur=u.get("GranuleUR") or Path(url).name, url=url,
                           size_mb=float(archive[0].get("Size") or 0.0), produced=produced,
                           product=product)
    return out


def survey_window(start: dt.date, end: dt.date, *,
                  product: str = DEFAULT_PRODUCT) -> tuple[dict[dt.date, Granule], list[dt.date]]:
    """Granules for a window under a run policy: ``auto`` = Late, else Early (head).

    Returns the chosen granule per day and the days with no granule in either run. With
    ``product="auto"`` a day is served by the Late Run as soon as its granule exists
    (~14.5 h after the day closes, measured) and by the Early Run (~4.5 h) until then, so
    the rolling window is complete *and* the best available run wins as soon as it lands.
    Each day's ``Granule.product`` records which run served it.
    """
    if product != "auto":
        granules = survey(start, end, product=product)
        return granules, [d for d in _daterange(start, end) if d not in granules]
    late = survey(start, end, product="late")
    early = survey(start, end, product="early")
    chosen = {d: (late.get(d) or early[d]) for d in early.keys() | late.keys()}
    missing = [d for d in _daterange(start, end) if d not in chosen]
    return chosen, missing


def _opener(creds: tuple[str, str]) -> urllib.request.OpenerDirector:
    """Opener that sends Basic auth *pre-emptively*, but only to :data:`AUTH_HOSTS`.

    GES DISC answers an unauthenticated GET with a 302 to URS rather than a 401, so an
    auth handler driven by challenges would never attach the password; injecting it up
    front (host-restricted) is what makes a netrc-only setup work without ``requests``.
    """
    token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()

    class _PreemptiveAuth(urllib.request.BaseHandler):
        def https_request(self, req):  # noqa: N802 - urllib handler naming
            host = urllib.parse.urlsplit(req.full_url).hostname or ""
            if host in AUTH_HOSTS and "Authorization" not in req.headers:
                req.add_header("Authorization", f"Basic {token}")
            return req

    return urllib.request.build_opener(_PreemptiveAuth(),
                                       urllib.request.HTTPCookieProcessor(
                                           http.cookiejar.CookieJar()))


def _read_capped(resp, limit: int, url: str) -> bytes:
    """Read a whole response body, bounded — an endpoint that never reaches EOF cannot
    grow the body past ``limit`` (it would otherwise be buffered in memory unbounded)."""
    buf = bytearray()
    while True:
        chunk = resp.read(1 << 20)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > limit:
            raise ImergError(f"{url} sent more than {limit} bytes — not a valid response")


def _drop_oversized_part(part: Path) -> None:
    """Delete a ``.part`` that is already past the granule size cap (never resume junk)."""
    try:
        if part.stat().st_size > MAX_GRANULE_BYTES:
            part.unlink(missing_ok=True)
    except OSError:
        pass


def _copy_capped(src, out, *, limit: int, already: int = 0, url: str = "") -> int:
    """Bounded ``shutil.copyfileobj`` with an explicit EOF test.

    ``copyfileobj`` trusts ``read()`` to return ``b""`` at the end of the body; if a
    server (or a test stub) never does, it appends to ``out`` forever. Returns bytes
    written and raises :class:`ImergError` as soon as the body passes ``limit`` bytes.
    """
    written = 0
    while True:
        chunk = src.read(1 << 20)
        if not chunk:
            return written
        out.write(chunk)
        written += len(chunk)
        if already + written > limit:
            raise ImergError(f"{url or 'response'} exceeded the granule size cap "
                             f"({already + written} bytes > {limit}) — refusing to write more")


def _looks_like_granule(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return head.startswith(_HDF5_MAGIC) or head.startswith(_NC_MAGIC)


def download_granule(url: str, dest: Path, *, creds: tuple[str, str] | None = None,
                     overwrite: bool = False, route: str = DEFAULT_ROUTE,
                     log: Callable[[str], None] | None = None) -> Path:
    """Download one granule to ``dest`` atomically (``.part`` + rename); resumable.

    ``route="auto"`` (default) tries the AWS Open Data mirror first — same-region S3 with
    temporary credentials from the Earthdata-backed ``s3credentials`` endpoint — and falls
    back to the GES DISC HTTPS endpoint. Both transports write to ``<dest>.part`` and
    resume it with a byte-range request, and both reject a body that is not HDF5/NetCDF
    (an HTML login page, an error document) instead of caching it.
    """
    if route not in ROUTES:
        raise ImergError(f"unknown route {route!r}; use {'|'.join(ROUTES)}")
    order = {"auto": ("s3", "https"), "s3": ("s3",), "https": ("https",)}[route]
    geters = {"s3": _get_s3, "https": _get_https}
    dest = Path(dest)
    if dest.exists() and not overwrite:
        if _looks_like_granule(dest):
            return dest
        dest.unlink()
    creds = creds if creds is not None else earthdata_credentials()
    if creds is None:
        raise ImergAuthError(_AUTH_STEPS)
    last: Exception | None = None
    for name in order:
        try:
            return geters[name](url, dest, creds, overwrite=overwrite)
        except ImergAuthError:
            raise                      # a login problem is not a transport problem
        except (ImergError, OSError) as exc:
            last = exc
            if len(order) > 1 and log:
                log(f"{name} route failed for {Path(url).name}: "
                    f"{type(exc).__name__}: {exc}" + (" — trying the next route" if name != order[-1] else ""))
    raise ImergError(f"all IMERG routes failed for {url} ({last})")


def _get_https(url: str, dest: Path, creds: tuple[str, str], *,
               overwrite: bool = False) -> Path:
    """GES DISC HTTPS download (the ``data.gesdisc.earthdata.nasa.gov`` route)."""
    opener = _opener(creds)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _drop_oversized_part(part)
    offset = part.stat().st_size if part.exists() else 0
    last: Exception | None = None
    for attempt in range(DOWNLOAD_TRIES):
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            _GATE.wait()
            req = urllib.request.Request(url, headers={"User-Agent": "ewb-live/imerg", **headers})
            with opener.open(req, timeout=READ_TIMEOUT_S) as resp:
                if offset and getattr(resp, "status", 206) == 200:   # range ignored
                    offset = 0
                try:
                    with open(part, "ab" if offset else "wb") as out:
                        _copy_capped(resp, out, limit=MAX_GRANULE_BYTES, already=offset, url=url)
                except ImergError:
                    part.unlink(missing_ok=True)   # a body that busts the cap is junk:
                    raise                          # never let the next run resume it
            if not _looks_like_granule(part):
                size = part.stat().st_size
                part.unlink(missing_ok=True)       # an HTML login page / error document is
                raise ImergError(                  # not a resumable partial either
                    f"{url} did not return a NetCDF/HDF5 granule ({size} bytes "
                    "received) — check the Earthdata credentials and that the granule exists")
            os.replace(part, dest)
            return dest
        except ImergAuthError:
            raise
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (401, 403):
                raise ImergAuthError(
                    f"GES DISC rejected the Earthdata credentials for {url} (HTTP {exc.code}).\n"
                    + _AUTH_STEPS) from exc
            if exc.code == 416:                      # .part already complete/corrupt
                part.unlink(missing_ok=True)
                offset = 0
                continue
            if exc.code not in _RETRY_CODES:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            offset = part.stat().st_size if part.exists() else 0
        time.sleep(min(60.0, 2.0 ** attempt) + random.uniform(0.0, 0.5))
    raise ImergError(f"download failed after {DOWNLOAD_TRIES} tries: {url} ({last})")


# ------------------------------------------------------- AWS Open Data (S3) transport

_S3_LOCK = threading.Lock()
_S3_CACHE: dict = {"creds": None, "fs": None, "expires": 0.0}


def s3_credentials(creds: tuple[str, str] | None = None, *, force: bool = False,
                   now: float | None = None) -> dict | None:
    """Temporary S3 credentials for the AWS Open Data mirror (cached, thread-safe).

    These are what makes the AWS route work: the registry lists the IMERG buckets as
    ``ControlledAccess``, so unsigned S3 requests are refused and GES DISC hands out
    hour-long credentials to an authenticated Earthdata user instead. ``None`` means
    "no Earthdata login configured"; a login that is refused raises :class:`ImergAuthError`.
    """
    now = time.time() if now is None else now
    with _S3_LOCK:
        if not force and _S3_CACHE["creds"] and now < _S3_CACHE["expires"]:
            return _S3_CACHE["creds"]
    creds = creds if creds is not None else earthdata_credentials()
    if creds is None:
        return None
    try:
        raw = _http_get(S3_CREDENTIALS_URL, opener=_opener(creds), tries=3)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ImergAuthError(
                f"{S3_CREDENTIALS_URL} rejected the Earthdata credentials (HTTP {exc.code}).\n"
                + _AUTH_STEPS) from exc
        raise
    data = json.loads(raw)
    if not {"access_key_id", "secret_access_key", "session_token"} <= set(data):
        raise ImergError(f"unexpected s3credentials payload: {sorted(data)}")
    expires = now + 55 * 60.0
    try:
        stamp = dt.datetime.fromisoformat(str(data.get("expiration", "")).replace("Z", "+00:00"))
        expires = stamp.timestamp() - 120.0
    except ValueError:
        pass
    with _S3_LOCK:
        _S3_CACHE.update(creds=data, fs=None, expires=expires)
        return _S3_CACHE["creds"]


def s3_key(url: str) -> str:
    """Bucket key of a granule: the object path behind a GES DISC https or s3 URL."""
    if url.startswith("s3://"):
        return url.split("://", 1)[1].split("/", 1)[1] if "/" in url.split("://", 1)[1] else ""
    path = urllib.parse.urlsplit(url).path.lstrip("/")
    return path[len("data/"):] if path.startswith("data/") else path


def _s3_filesystem(tmp_creds: dict):
    import s3fs

    with _S3_LOCK:
        if _S3_CACHE["fs"] is not None and _S3_CACHE["creds"] is tmp_creds:
            return _S3_CACHE["fs"]
    fs = s3fs.S3FileSystem(
        key=tmp_creds["access_key_id"], secret=tmp_creds["secret_access_key"],
        token=tmp_creds["session_token"], anon=False,
        client_kwargs={"region_name": tmp_creds.get("region") or S3_REGION},
        use_threads=False, block_size=8 << 20, cache_type="none", config_kwargs=_botocore_timeouts(),
    )
    with _S3_LOCK:
        _S3_CACHE["fs"] = fs
    return fs


def _botocore_timeouts() -> dict:
    return {"connect_timeout": CONNECT_TIMEOUT_S, "read_timeout": READ_TIMEOUT_S,
            "retries": {"max_attempts": 3, "mode": "standard"}}


def _get_s3(url: str, dest: Path, creds: tuple[str, str], *,
            overwrite: bool = False) -> Path:
    """AWS Open Data S3 download (unsigned access is refused; see :func:`s3_credentials`)."""
    key = s3_key(url)
    if not key:
        raise ImergError(f"cannot derive an S3 key from {url!r}")
    tmp = s3_credentials(creds, force=False)
    if tmp is None:
        raise ImergError("no Earthdata login configured, so no s3credentials for the "
                         "AWS Open Data mirror")
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _drop_oversized_part(part)
    offset = part.stat().st_size if part.exists() else 0
    obj = f"{tmp.get('bucket') or S3_BUCKET}/{key}"
    last: Exception | None = None
    for attempt in range(DOWNLOAD_TRIES):
        try:
            _GATE.wait()
            fs = _s3_filesystem(tmp)
            with fs.open(obj, "rb") as src:
                if offset:
                    src.seek(offset)
                try:
                    with open(part, "ab" if offset else "wb") as out:
                        _copy_capped(src, out, limit=MAX_GRANULE_BYTES, already=offset, url=obj)
                except ImergError:
                    part.unlink(missing_ok=True)
                    raise
            if not _looks_like_granule(part):
                size = part.stat().st_size
                part.unlink(missing_ok=True)
                raise ImergError(f"{obj} is not a NetCDF/HDF5 granule "
                                 f"({size} bytes received)")
            os.replace(part, dest)
            return dest
        except FileNotFoundError:
            raise ImergError(f"{obj} not found in the AWS mirror (the CMR granule listing "
                             "may be ahead of the copy)")
        except (PermissionError, OSError) as exc:
            last = exc
            if "AccessDenied" in str(exc) or "ExpiredToken" in str(exc) \
                    or "InvalidAccessKeyId" in str(exc):
                tmp = s3_credentials(creds, force=True) or tmp   # rotate and retry once
        time.sleep(min(60.0, 2.0 ** attempt) + random.uniform(0.0, 0.5))
    raise ImergError(f"S3 download failed after {DOWNLOAD_TRIES} tries: {obj} ({last})")


# ------------------------------------------------------------- granule → daily field


def _open_dataset(path: str | Path) -> xr.Dataset:
    """Open a granule/daily file, trying the engines this repo can rely on."""
    last: Exception | None = None
    for engine in ("h5netcdf", "netcdf4", None):
        try:
            return xr.open_dataset(path, engine=engine, mask_and_scale=False,
                                   decode_times=False)
        except Exception as exc:  # noqa: BLE001 - engine missing or cannot read this file
            last = exc
    raise ImergError(f"cannot open {path} ({last})")


def _named(ds: xr.Dataset, want: str) -> str:
    """Name of a lat/lon coordinate in a file that may use ``lat``/``lat_b``/``grid_lat_b``."""
    wanted = {"lat": ("lat", "latitude", "lat_b", "grid_lat_b"),
              "lon": ("lon", "longitude", "lon_b", "grid_lon_b")}[want]
    names = [str(n) for n in ds.coords] + [str(n) for n in ds.variables]
    for cand in wanted:
        if cand in names:
            return cand
    raise ImergError(f"no {want} coordinate found (have {sorted(set(names))})")




def _check_grid(ds: xr.Dataset, src: str | Path, *, deg: float = IMERG_GRID_DEG) -> dict:
    """Assert the file sits on the expected global 0.1° grid; return coordinate names."""
    lat_name, lon_name = _named(ds, "lat"), _named(ds, "lon")
    lat = np.asarray(ds[lat_name].values, dtype="float64")
    lon = np.asarray(ds[lon_name].values, dtype="float64")
    if lat.size < 2 or lon.size < 2:
        raise ImergError(f"{src}: degenerate grid {lat.size}x{lon.size}")
    for name, arr in (("lat", lat), ("lon", lon)):
        step = float(np.median(np.diff(arr)))
        if abs(abs(step) - deg) > 1e-6:
            raise ImergError(f"{src}: {name} spacing {step} deg, expected {deg}")
        if not np.allclose(np.diff(arr), step, atol=1e-6):
            raise ImergError(f"{src}: {name} is not a regular lattice")
    if lat.min() > -90.0 + deg or lat.max() < 90.0 - deg \
            or lon.min() > -180.0 + deg or lon.max() < 180.0 - deg:
        raise ImergError(f"{src}: not a global grid (lat {lat.min()}..{lat.max()}, "
                         f"lon {lon.min()}..{lon.max()})")
    return {"lat": lat_name, "lon": lon_name,
            "nlat": int(lat.size), "nlon": int(lon.size),
            "lat_range": [float(lat.min()), float(lat.max())],
            "lon_range": [float(lon.min()), float(lon.max())]}


def _unit_factor(units: str, hours: float = HOURS_PER_DAY) -> float:
    """Multiplier that turns the producer's values into mm/day, from its own ``units``.

    The IMERG daily product stores a mean sampling-weighted *rate* (mm/hr), so the daily
    accumulation is rate × 24 h. The factor is taken from the file rather than assumed:
    an unrecognised unit string raises instead of guessing.
    """
    u = " ".join(str(units).lower().replace("_", " ").split())
    if any(k in u for k in ("mm/day", "mm d-1", "mm/d")):
        return 1.0
    if any(k in u for k in ("mm/hr", "mm/h", "mm/hour", "mm hr-1", "mm h-1")):
        return float(hours)
    raise ImergError(f"cannot convert IMERG precipitation with units {units!r} to mm/day "
                     "(expected a 'mm/hr' mean rate or a 'mm/day' accumulation)")


def _granule_day(src: Path, ds: xr.Dataset) -> dt.date:
    """UTC day of a granule: the filename's YYYYMMDD, else its ``time`` variable."""
    m = _DAY_IN_NAME.search(src.name)
    if m:
        return dt.datetime.strptime(m.group(1), "%Y%m%d").date()
    if "time" in ds.variables:
        t = np.asarray(ds["time"].values, dtype="float64").ravel()
        units = str(ds["time"].attrs.get("units", ""))
        base = units.split(" since ")
        if t.size and len(base) == 2 and "days" in base[0]:
            epoch = dt.datetime.fromisoformat(base[1].strip().replace("UTC", "").strip())
            return (epoch + dt.timedelta(days=float(t[0]))).date()
    raise ImergError(f"{src}: cannot determine the UTC day from the name or 'time'")


def _grid_field(ds: xr.Dataset, name: str, lat_name: str, lon_name: str,
                shape: tuple[int, int]) -> np.ndarray:
    """Variable as a (lat, lon) float64 array in the file's coordinate order."""
    if name not in ds.variables:
        raise ImergError(f"no '{name}' variable (have {sorted(map(str, ds.data_vars))})")
    var = ds[name]
    extra = [str(d) for d in var.dims if str(d) not in (lat_name, lon_name)]
    if any(str(d) != "time" for d in extra):
        raise ImergError(f"'{name}' has unexpected dimensions {var.dims}")
    out = np.squeeze(np.asarray(var.values, dtype="float64"))   # time is length 1
    order = [str(d) for d in var.dims if str(d) in (lat_name, lon_name)]
    if order == [lon_name, lat_name]:
        out = out.T
    if out.shape != shape:
        raise ImergError(f"'{name}' is {out.shape}, expected {shape}")
    return out


def daily_from_granule(path: str | Path, *, hours: float = HOURS_PER_DAY,
                       grid_deg: float = IMERG_GRID_DEG) -> xr.Dataset:
    """Producer granule → daily Dataset: ``precipitation`` in mm/day + completeness vars.

    * units come from the granule, never from an assumption. The V07 daily product is
      built by the producer as ``mean(valid half-hourly rate in mm/hr) x 24``, i.e. the
      stored value is a **daily accumulation**, and the file's own ``units`` attribute is
      what decides whether this function multiplies by 24 or not. A global-mean
      plausibility band (0.05-25 mm/day, world-average ~2.6) then turns a mislabelled
      ``units`` attribute — a 24x error — into a refused ingest instead of a silently
      corrupt record;
    * non-positive (producer fill, e.g. −9999) and non-finite cells → NaN, never 0;
    * ``precipitation_cnt`` (uint8, how many of the 48 half-hourly estimates the daily
      mean is based on; 255 = not published) and ``probabilityLiquidPrecipitation``
      (uint8 %: 0 missing, 1 likely solid, 100 likely liquid *or* no precipitation) are
      kept as companion masks so the consumer decides the screening.
    """
    src = Path(path)
    with _open_dataset(src) as ds:
        grid = _check_grid(ds, src, deg=grid_deg)
        lat_name, lon_name = grid["lat"], grid["lon"]
        shape = (grid["nlat"], grid["nlon"])
        units = str(ds["precipitation"].attrs.get("units", ""))
        factor = _unit_factor(units, hours)
        rate = _grid_field(ds, "precipitation", lat_name, lon_name, shape)
        lat = np.asarray(ds[lat_name].values, dtype="float64")
        lon = np.asarray(ds[lon_name].values, dtype="float64")
        day = _granule_day(src, ds)
        if "precipitation_cnt" in ds.variables:
            cnt = _grid_field(ds, "precipitation_cnt", lat_name, lon_name, shape)
            cnt = np.where((cnt >= 0) & np.isfinite(cnt), cnt, CNT_UNKNOWN).astype("uint8")
        else:
            cnt = np.full(shape, CNT_UNKNOWN, dtype="uint8")
        if "probabilityLiquidPrecipitation" in ds.variables:
            pol = _grid_field(ds, "probabilityLiquidPrecipitation", lat_name, lon_name, shape)
            pol = np.where((pol >= 0) & np.isfinite(pol), pol, 0.0).astype("uint8")
        else:
            pol = np.zeros(shape, dtype="uint8")

    values = np.where(np.isfinite(rate) & (rate > 0.0), rate, np.nan).astype("float32") * np.float32(factor)
    peak = float(np.nanmax(values)) if values.size else 0.0
    mean = float(np.nanmean(values)) if np.isfinite(values).any() else float("nan")
    lo, hi = MEAN_MM_DAY_BOUNDS
    if peak > MAX_PLAUSIBLE_MM_DAY:
        raise ImergError(f"{src}: implausible daily accumulation (max {peak:.0f} mm/day) "
                         f"with producer units {units!r} — refusing to store it")
    if np.isfinite(mean) and not lo <= mean <= hi:
        hint = ("the field looks 24x too large: the value is probably already mm/day while "
                "the file says a rate — re-run with hours=1.0 (or fix the units mapping)" if mean > hi
                else "the field looks 24x too small: the value is probably a mm/hr mean rate "
                     "while the file says mm/day — re-run with hours=24.0")
        raise ImergError(f"{src}: global daily-mean precipitation {mean:.3f} mm/day is outside "
                         f"the plausible band {lo}-{hi} mm/day (units {units!r}, factor {factor}). {hint}")
    out = xr.Dataset(
        {
            "precipitation": (("time", "lat", "lon"), values[np.newaxis],
                              {"units": UNITS, "source_units": units,
                               "conversion_factor": float(factor),
                               "long_name": "daily precipitation accumulation"}),
            "precipitation_cnt": (("time", "lat", "lon"), cnt[np.newaxis],
                                  {"units": "count",
                                   "comment": f"half-hourly estimates (0-{STEPS_PER_DAY}) "
                                              f"behind the daily mean; {CNT_UNKNOWN} = not "
                                              "published by the producer"}),
            "probabilityLiquidPrecipitation": (("time", "lat", "lon"), pol[np.newaxis],
                                               {"units": "%",
                                                "comment": "0 missing, 1 likely solid, "
                                                           "100 likely liquid or no precip"}),
        },
        coords={"time": pd.to_datetime([dt.datetime.combine(day, dt.time())]),
                "lat": ("lat", lat), "lon": ("lon", lon)},
        attrs={"source": src.name, "day": f"{day:%Y-%m-%d}", "units": UNITS,
               "producer_units": units, "grid_deg": grid_deg,
               # netCDF attributes must be scalars/strings — a dict here made every daily
               # write fail (h5netcdf refuses non-serialisable attrs), so the grid summary
               # travels as JSON and is parsed back with json.loads.
               "grid": json.dumps(grid, sort_keys=True),
               "global_mean_mm_day": round(mean, 4) if np.isfinite(mean) else None,
               "schema_version": SCHEMA_VERSION},
    )
    return out


def accumulate_half_hourly(da: xr.DataArray, *, step_hours: float = 0.5,
                           steps_per_day: int = STEPS_PER_DAY) -> tuple[xr.DataArray, xr.DataArray]:
    """Half-hourly rate field (mm/hr, UTC ``time``) → daily accumulation (mm/day) + step count.

    Day boundary: the **UTC calendar day** of each step's timestamp. Half-hourly granule
    stamps are the *start* of their window (``S000000`` … ``S233000``), so bucketing by
    ``time.dt.date`` puts exactly ``steps_per_day`` steps in each UTC day.

    Returns ``(accumulation, n_steps)`` both on a daily time axis (00:00 UTC). NaN steps
    are skipped in the sum *and* counted in ``n_steps``: a day whose steps are all NaN
    yields NaN (not 0) with ``n_steps == 0``, and a day with a dropped step shows up as
    ``n_steps < steps_per_day`` — the partial-day signal, never a silent zero.
    """
    if "time" not in da.dims:
        raise ImergError(f"need a 'time' dimension, got {da.dims}")
    times = pd.DatetimeIndex(pd.to_datetime(da.time.values))
    if times.tz is not None:
        times = times.tz_convert("UTC").tz_localize(None)
    if times.size > 1:
        step = pd.Timedelta(np.median(np.diff(times)))
        if abs(step.total_seconds() - step_hours * 3600.0) > 1.0:
            raise ImergError(f"time step is {step}, expected {step_hours} h")
    days = times.normalize()
    data = da.copy(data=np.asarray(da.values, dtype="float32"))
    days_coord = xr.DataArray(days.values, dims="time", name="day")
    accum = (data * np.float32(step_hours)).groupby(days_coord).sum("time", min_count=1)
    n_steps = data.notnull().groupby(days_coord).sum("time")
    accum = accum.rename(day="time").astype("float32")
    n_steps = n_steps.rename(day="time").astype("uint8")
    accum.attrs.update({"units": UNITS, "step_hours": step_hours, "steps_per_day": steps_per_day,
                        "day_boundary": "UTC calendar day of the step timestamp",
                        "comment": "sum of half-hourly rate x step over the UTC calendar day"})
    n_steps.attrs.update({"units": "count",
                          "comment": f"half-hourly steps contributing (max {steps_per_day}); "
                                     "less than that is a partial day"})
    return accum, n_steps


# ------------------------------------------------------------------ cache layout


def _root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else imerg_root()


def daily_dir(root: str | Path | None = None) -> Path:
    return _root(root) / "daily"


def raw_dir(root: str | Path | None = None, product: str = "early") -> Path:
    return _root(root) / "raw" / product_for(product).collection_dir


def status_path(root: str | Path | None = None) -> Path:
    return _root(root) / "status.json"


def daily_path(day: dt.date, root: str | Path | None = None) -> Path:
    return daily_dir(root) / f"imerg_{day:%Y%m%d}.nc"


def raw_path(granule: Granule, root: str | Path | None = None,
             product: str | None = None) -> Path:
    """Cached producer file for a granule (kept per collection, so runs never mix)."""
    prod = product_for(product or granule.product)
    return raw_dir(root, prod.key) / f"{granule.day:%Y/%m}" / granule.filename


def _daterange(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)] if end >= start else []


def _read_status(root: str | Path | None = None) -> dict:
    path = status_path(root)
    if not path.exists():
        return {"schema": SCHEMA_VERSION, "product": DEFAULT_PRODUCT, "days": {}}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"schema": SCHEMA_VERSION, "product": DEFAULT_PRODUCT, "days": {}}
    data.setdefault("days", {})
    return data


def _write_status(root: str | Path | None = None, *, status: dict) -> None:
    path = status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    status["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _status_entry(root: str | Path | None, day: dt.date, **fields) -> None:
    status = _read_status(root)
    entry = status["days"].get(f"{day:%Y-%m-%d}", {})
    entry.update(fields)
    status["days"][f"{day:%Y-%m-%d}"] = entry
    status["product"] = entry.get("product", status.get("product", DEFAULT_PRODUCT))
    _write_status(root, status=status)


def _netcdf_attrs(ds: xr.Dataset) -> xr.Dataset:
    """Copy of ``ds`` whose global attrs are all netCDF-serialisable (dicts -> JSON)."""
    out = ds.copy()
    for key, value in out.attrs.items():
        if isinstance(value, dict):
            out.attrs[key] = json.dumps(value, sort_keys=True)
    return out


def _write_netcdf(day: dt.date, ds: xr.Dataset, path: Path) -> int:
    """Compressed per-day NetCDF4, written atomically (``.part`` + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    nlat, nlon = ds.sizes["lat"], ds.sizes["lon"]
    chunks = (1, max(1, min(180, nlat)), max(1, min(360, nlon)))
    encoding = {
        "precipitation": {"dtype": "float32", "zlib": True, "complevel": 4, "chunksizes": chunks,
                          "_FillValue": np.float32(np.nan)},
        "precipitation_cnt": {"dtype": "uint8", "zlib": True, "complevel": 4,
                             "chunksizes": chunks},
        "probabilityLiquidPrecipitation": {"dtype": "uint8", "zlib": True, "complevel": 4,
                                           "chunksizes": chunks},
    }
    last: Exception | None = None
    for engine in ("h5netcdf", "netcdf4"):
        try:
            _netcdf_attrs(ds).to_netcdf(part, engine=engine, encoding=encoding, mode="w")
            os.replace(part, path)
            return path.stat().st_size
        except Exception as exc:  # noqa: BLE001 - try the other engine
            last = exc
            part.unlink(missing_ok=True)
    raise ImergError(f"cannot write {path} ({last})")


def ingest_day(src: str | Path | Granule, day: dt.date | None = None, *,
               root: str | Path | None = None, product: str | None = None,
               hours: float = HOURS_PER_DAY, grid_deg: float = IMERG_GRID_DEG,
               overwrite: bool = False, upgrade: bool = True) -> dict:
    """Granule file → ``daily/imerg_<YYYYMMDD>.nc`` + a ``status.json`` entry.

    ``upgrade`` rebuilds a cached day when a *better run* has since been published (the
    Early Run head day of a ``product="auto"`` window becomes the Late Run once Late
    exists), so the window converges on the preferred run without losing days.
    """
    src_path = Path(src.url if isinstance(src, Granule) else src)
    run = product or (src.product if isinstance(src, Granule) else "early")
    day = day or _granule_day(src_path, _open_dataset(src_path))
    out = daily_path(day, root)
    if out.exists() and not overwrite:
        cached = (_read_status(root)["days"].get(f"{day:%Y-%m-%d}", {}) or {})
        if not upgrade or cached.get("product") in (None, run):
            # carry the cached numbers so an incremental run still reports a window mean
            return {"day": f"{day:%Y-%m-%d}", "status": "already",
                    "product": cached.get("product") or run, "bytes": out.stat().st_size,
                    "mean_mm_day": cached.get("mean_mm_day"),
                    "max_mm_day": cached.get("max_mm_day")}
    ds = daily_from_granule(src_path, hours=hours, grid_deg=grid_deg)
    values = ds["precipitation"].values
    cnt = ds["precipitation_cnt"].values
    valid = np.isfinite(values)
    full = (cnt == STEPS_PER_DAY) | (cnt == CNT_UNKNOWN)
    nbytes = _write_netcdf(day, ds, out)
    known = cnt != CNT_UNKNOWN
    status = ("present" if valid.all() and (full.all() or not known.any()) else "partial")
    entry = {
        "status": status,
        "product": run,
        "granule": src_path.name,
        "available": True,
        "bytes": int(nbytes),
        "mean_mm_day": round(float(np.nanmean(values)) if valid.any() else float("nan"), 4),
        "max_mm_day": round(float(np.nanmax(values)) if valid.any() else float("nan"), 3),
        "frac_valid": round(float(valid.mean()), 6),
        "frac_full_sampling": round(float(full.mean()), 6),
        "cnt_min": int(cnt[known].min()) if known.any() else None,
        "ingested_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _status_entry(root, day, **entry)
    return {"day": f"{day:%Y-%m-%d}", "status": status, "bytes": int(nbytes), **entry}


# ------------------------------------------------------------------- fetch/ingest


def fetch(days: Iterable[dt.date], *, root: str | Path | None = None,
          product: str = DEFAULT_PRODUCT, granules: dict[dt.date, Granule] | None = None,
          workers: int = 2, overwrite: bool = False,
          downloader: Callable[[str, Path], Path] | None = None,
          creds: tuple[str, str] | None | str = "auto", route: str = DEFAULT_ROUTE,
          log: Callable[[str], None] | None = None) -> dict:
    """Download the granules for ``days`` into ``raw/`` (idempotent, resumable, polite).

    ``downloader(url, dest)`` is injectable (tests, alternative transports); ``granules``
    lets a caller reuse one :func:`survey_window` for many days. Days with no published
    granule are reported as ``missing`` — a data gap, never a zero. ``route="auto"``
    prefers the AWS Open Data mirror and falls back to GES DISC HTTPS.
    """
    days = sorted(set(days))
    if not days:
        return {"fetched": 0, "cached": 0, "missing": [], "errors": {}, "bytes": 0}
    if granules is None:
        granules, _ = survey_window(days[0], days[-1], product=product)
    if creds == "auto":
        creds = earthdata_credentials()
    downloader = downloader or (lambda url, dest: download_granule(url, dest, creds=creds,
                                                                   overwrite=overwrite,
                                                                   route=route, log=log))
    todo, cached, missing = [], 0, []
    for day in days:
        gran = granules.get(day)
        if gran is None:
            missing.append(day)
            continue
        dest = raw_path(gran, root)
        if dest.exists() and _looks_like_granule(dest) and not overwrite:
            cached += 1
            continue
        todo.append((day, gran, dest))

    done: list[tuple[dt.date, Granule, Path]] = []
    errors: dict[str, str] = {}

    def _get(item):
        day, gran, dest = item
        try:
            return day, gran, downloader(gran.url, dest), None
        except Exception as exc:  # noqa: BLE001 - one bad day must not kill the window
            return day, gran, None, f"{type(exc).__name__}: {exc}"

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
            for day, gran, path, error in pool.map(_get, todo):
                if error:
                    errors[f"{day:%Y-%m-%d}"] = error
                    if log:
                        log(f"ERROR {day:%Y-%m-%d}: {error}")
                    continue
                done.append((day, gran, path))
                if log:
                    log(f"fetched {day:%Y-%m-%d} {gran.filename} "
                        f"({path.stat().st_size / 1e6:.1f} MB)")
    for day in missing:
        _status_entry(root, day, status="missing", available=False, product=product,
                      surveyed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        if log:
            log(f"missing  {day:%Y-%m-%d} — no granule published yet")
    return {"fetched": len(done), "cached": cached,
            "missing": [f"{d:%Y-%m-%d}" for d in missing], "errors": errors,
            "bytes": sum(p.stat().st_size for _, _, p in done)}


def ingest(start: dt.date, end: dt.date, *, root: str | Path | None = None,
           product: str = DEFAULT_PRODUCT, workers: int = 2, keep_raw: int = DEFAULT_KEEP_RAW,
           keep_days: int = DEFAULT_KEEP_DAYS, overwrite: bool = False,
           now: dt.datetime | None = None,
           granules: dict[dt.date, Granule] | None = None,
           downloader: Callable[[str, Path], Path] | None = None,
           route: str = DEFAULT_ROUTE, grid_deg: float = IMERG_GRID_DEG,
           batch_days: int | None = None, min_free: int = MIN_FREE_BYTES,
           log: Callable[[str], None] | None = None) -> dict:
    """Survey → download → daily mm/day fields + completeness index, then roll the window.

    Safe to re-run: existing granules and daily files are reused (``overwrite`` forces a
    re-fetch/rebuild), an interrupted run resumes from its ``.part`` files, and a day that
    a better run has since covered is rebuilt from that run (``product="auto"``).

    The window is fetched in batches of ``batch_days`` days (default ``keep_raw + 2``) with
    ``raw/`` rolled between them, so peak usage stays at roughly one batch of granules plus
    the daily store, and :func:`require_headroom` refuses the run up front if even that
    would eat into the :data:`MIN_FREE_BYTES` the live service needs.
    """
    days = _daterange(start, end)
    if granules is None:
        granules, _ = survey_window(start, end, product=product)
    batch_days = max(1, keep_raw + 2 if batch_days is None else batch_days)
    pending = [d for d in days if d in granules and not raw_path(granules[d], root).exists()]
    if pending:
        require_headroom(root, project_window(days, granules, batch_days=batch_days)["net"],
                         min_free=min_free)
    got = {"fetched": 0, "cached": 0, "missing": [], "errors": {}, "bytes": 0}
    entries: list[dict] = []
    rolled: dict = {}
    for i in range(0, len(days), batch_days):
        batch = days[i:i + batch_days]
        one = fetch(batch, root=root, product=product, granules=granules, workers=workers,
                    overwrite=overwrite, downloader=downloader, route=route, log=log)
        for key in ("fetched", "cached", "bytes"):
            got[key] += one[key]
        got["missing"] += one["missing"]
        got["errors"].update(one["errors"])
        for day in batch:
            gran = granules.get(day)
            if gran is None:
                continue
            src = raw_path(gran, root)
            if not src.exists():
                continue
            try:
                entries.append(ingest_day(gran, day, root=root, product=gran.product,
                                          grid_deg=grid_deg, overwrite=overwrite))
            except ImergError as exc:
                _status_entry(root, day, status="error", error=str(exc), product=product)
                if log:
                    log(f"ERROR {day:%Y-%m-%d}: {exc}")
        rolled = roll_window(root=root, keep_days=keep_days, keep_raw=keep_raw, now=now)
    means = [e.get("mean_mm_day") for e in entries if e.get("mean_mm_day") is not None]
    return {"window": [f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"], "product": product,
            "granules_available": len([d for d in days if d in granules]),
            "fetch": got, "ingested": entries, "rolled": rolled,
            "global_mean_mm_day": round(float(np.mean(means)), 3) if means else None,
            "disk": disk_usage(root)}


def free_bytes(root: str | Path | None = None) -> int:
    """Bytes free on the filesystem that will hold the cache (walks up to an existing dir)."""
    path = _root(root)
    while not path.exists() and path != path.parent:
        path = path.parent
    return int(shutil.disk_usage(path).free)


def project_window(days: Iterable[dt.date], granules: dict[dt.date, Granule], *,
                   batch_days: int = DEFAULT_BATCH_DAYS) -> dict:
    """Projected **peak** disk cost of a window (what the headroom guard uses).

    ``ingest`` works in batches of ``batch_days`` days and rolls ``raw/`` between them, so
    the high-water mark is one batch of granules plus the whole daily store of the window
    — not the full raw backfill. That is what lets a 90-day backfill fit on a filesystem
    that is already 98-100 % full.
    """
    days = [d for d in sorted(set(days)) if d in granules]

    def size(day: dt.date) -> int:
        return int(granules[day].size_mb * 1e6) if granules[day].size_mb else EST_RAW_BYTES

    step = max(1, batch_days)
    batches = [days[i:i + step] for i in range(0, len(days), step)] or [[]]
    peak_raw = max(sum(size(d) for d in b) for b in batches)
    daily_new = len(days) * EST_DAILY_BYTES
    return {"raw_total": sum(size(d) for d in days), "peak_raw": peak_raw,
            "daily_new": daily_new, "net": peak_raw + daily_new,
            "batches": len(batches), "batch_days": step}


def require_headroom(root: str | Path | None = None, need: int = 0, *,
                     min_free: int = MIN_FREE_BYTES) -> int:
    """Raise unless the cache filesystem has ``min_free`` free *after* spending ``need``.

    A full root filesystem takes down the live service, so an ingest that cannot fit is
    an error, not a surprise: the message states the numbers and the two levers
    (``--keep-raw 0`` and a smaller ``--min-free-bytes``).
    """
    free = free_bytes(root)
    if free - need < min_free:
        raise ImergError(
            f"not enough free space for this fetch on {daily_dir(root).parent}: "
            f"{free / 1e9:.2f} GB free, this window needs ~{need / 1e9:.2f} GB and "
            f"{min_free / 1e9:.2f} GB must stay free (live service on the same filesystem). "
            f"Free space, or shrink the footprint: --keep-raw 0 (no cached granules), "
            f"fewer --days, or a deliberate --min-free-bytes override.")
    return free


def disk_usage(root: str | Path | None = None) -> dict:
    out = {}
    for name, path in (("daily", daily_dir(root)), ("raw", _root(root) / "raw")):
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0
        out[name] = round(total / 1e6, 1)
    out["total_mb"] = round(sum(out.values()), 1)
    return out


def roll_window(*, root: str | Path | None = None, keep_days: int = DEFAULT_KEEP_DAYS,
                keep_raw: int = DEFAULT_KEEP_RAW, now: dt.datetime | None = None,
                dry_run: bool = False) -> dict:
    """Keep only the last ``keep_days`` days of daily fields (and ``keep_raw`` granules).

    The rolling 90-day window from the owner spec; a day outside the window is deleted,
    never blanked. The daily fields are the cache of record, so raw granules are dropped
    almost immediately (``keep_raw`` days) to bound disk.
    """
    cutoff = ((now or dt.datetime.now(dt.timezone.utc)).date() - dt.timedelta(days=keep_days - 1))
    removed, freed = [], 0
    for path in sorted(daily_dir(root).glob("imerg_*.nc")) if daily_dir(root).exists() else []:
        try:
            day = dt.datetime.strptime(path.stem.split("_")[1], "%Y%m%d").date()
        except (IndexError, ValueError):
            continue
        if day < cutoff:
            freed += path.stat().st_size
            removed.append(str(path))
            if not dry_run:
                path.unlink()
    raw_cutoff = ((now or dt.datetime.now(dt.timezone.utc)).date()
                  - dt.timedelta(days=max(keep_raw, 0)))
    raw_removed = []
    for path in sorted((raw_dir(root).rglob("*.nc4")).__iter__()) if raw_dir(root).exists() else []:
        try:
            day = dt.datetime.strptime(_DAY_IN_NAME.search(path.name).group(1), "%Y%m%d").date()
        except AttributeError:
            continue
        if day < raw_cutoff:
            freed += path.stat().st_size
            raw_removed.append(str(path))
            if not dry_run:
                path.unlink()
    if not dry_run and removed:
        status = _read_status(root)
        for path in removed:
            status["days"].pop(f"{dt.datetime.strptime(Path(path).stem.split('_')[1], '%Y%m%d'):%Y-%m-%d}",
                               None)
        _write_status(root, status=status)
    return {"cutoff": f"{cutoff:%Y-%m-%d}", "removed": [Path(p).stem for p in removed],
            "removed_raw": [Path(p).name for p in raw_removed],
            "freed_bytes": freed, "dry_run": dry_run}


# --------------------------------------------------------------------- public API


def _normalize_screens(screen: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(screen, str):
        parts = [s.strip().lower() for s in screen.split("+") if s.strip()]
    else:
        parts = [str(s).strip().lower() for s in screen]
    out: list[str] = []
    for part in parts:
        if part in ("", "none"):
            continue
        if part not in ("cnt", "polid"):
            raise ImergError(f"unknown screen {part!r}; use any of {'+'.join(SCREENS)}")
        if part not in out:
            out.append(part)
    return tuple(out)


def _load_fields(start: dt.date, end: dt.date, *, root: str | Path | None = None,
                 product: str = DEFAULT_PRODUCT) -> dict:
    """Stack the daily files of a window into (day, lat, lon) arrays.

    Absent days are present on the time axis and all-NaN: the series is a regular daily
    grid, and :func:`load_imerg_status` / :func:`load_imerg_mask` say why a day is empty
    (never a silent zero).
    """
    days = _daterange(start, end)
    if not days:
        raise ImergError(f"empty window {start}..{end} (end before start)")
    precip = cnt = pol = None
    lat = lon = None
    present: list[dt.date] = []
    for i, day in enumerate(days):
        path = daily_path(day, root)
        if not path.exists():
            continue
        with _open_dataset(path) as ds:
            v = np.squeeze(np.asarray(ds["precipitation"].values, dtype="float32"))
            c = (np.squeeze(np.asarray(ds["precipitation_cnt"].values, dtype="uint8"))
                 if "precipitation_cnt" in ds.variables
                 else np.full(v.shape, CNT_UNKNOWN, dtype="uint8"))
            q = (np.squeeze(np.asarray(ds["probabilityLiquidPrecipitation"].values, dtype="uint8"))
                 if "probabilityLiquidPrecipitation" in ds.variables else np.zeros(v.shape, "uint8"))
            la = np.asarray(ds["lat"].values, dtype="float64")
            lo = np.asarray(ds["lon"].values, dtype="float64")
        if lat is None:
            shape = v.shape
            precip = np.full((len(days),) + shape, np.nan, dtype="float32")
            cnt = np.full((len(days),) + shape, CNT_UNKNOWN, dtype="uint8")
            pol = np.zeros((len(days),) + shape, dtype="uint8")
            lat, lon = la, lo
        elif la.shape != lat.shape or not np.array_equal(la, lat) or lo.shape != lon.shape \
                or not np.array_equal(lo, lon) or v.shape != precip.shape[1:]:
            raise ImergError(f"{path.name}: grid differs from the rest of the window "
                             "(mixed products/grids cannot be stacked)")
        precip[i] = v
        cnt[i] = c
        pol[i] = q
        present.append(day)
    if lat is None:
        raise ImergError(
            f"no IMERG daily fields in {start}..{end} under {daily_dir(root)} — fetch them "
            f"first: python -m ewb_live.sources.imerg update --days "
            f"{(end - start).days + 1} --product {product}")
    return {"days": days, "present": present, "precip": precip, "cnt": cnt, "pol": pol,
            "lat": lat, "lon": lon}


def _coords(fields: dict) -> dict:
    return {"time": pd.to_datetime([dt.datetime.combine(d, dt.time()) for d in fields["days"]]),
            "lat": ("lat", fields["lat"]), "lon": ("lon", fields["lon"])}


def load_imerg_daily(start: dt.date, end: dt.date, *, root: str | Path | None = None,
                     product: str = DEFAULT_PRODUCT, screen: str | Sequence[str] = "none",
                     min_cnt: int = STEPS_PER_DAY) -> xr.DataArray:
    """Daily IMERG precipitation, **(time, lat, lon) in mm/day**, for the UTC days
    ``start``…``end`` inclusive (time = 00:00 UTC of each day).

    NaN means *no observation* (missing granule, producer fill, or screened-out pixel) —
    never "no rain": use :func:`load_imerg_mask` or ``screen`` to make that explicit.

    ``screen``: ``"none"`` (default — keep every cell the producer considers valid),
    ``"cnt"`` (blank cells whose sampling is below ``min_cnt`` of the 48 half-hourly
    steps), ``"polid"`` (blank cells flagged likely-solid precipitation), or
    ``"cnt+polid"``. ``screen="cnt"`` with the default ``min_cnt=48`` is the
    completeness-clean choice for thresholding against the ERA5 climatology.
    """
    fields = _load_fields(start, end, root=root, product=product)
    values = fields["precip"].copy()
    screens = _normalize_screens(screen)
    if "cnt" in screens:
        keep = (fields["cnt"] >= min_cnt) | (fields["cnt"] == CNT_UNKNOWN)
        values = np.where(keep, values, np.nan).astype("float32")
    if "polid" in screens:
        values = np.where(fields["pol"] == 1, np.nan, values).astype("float32")
    missing = [f"{d:%Y-%m-%d}" for d in fields["days"] if d not in fields["present"]]
    da = xr.DataArray(
        values, dims=("time", "lat", "lon"), coords=_coords(fields),
        name="precipitation",
        attrs={"units": UNITS, "product": product, "run": run_label(product),
               "collection": product_for(product).short_name,
               "products": _products_breakdown(fields, root),
               "grid_deg": IMERG_GRID_DEG, "day_boundary": "UTC calendar day",
               "screens": list(screens), "min_cnt": int(min_cnt) if "cnt" in screens else None,
               "days_total": len(fields["days"]), "days_present": len(fields["present"]),
               "missing_days": missing,
               "source": "NASA GPM IMERG V07, GES DISC (AWS Open Data mirror when available)",
               "schema_version": SCHEMA_VERSION})
    return da


def _products_breakdown(fields: dict, root: str | Path | None) -> dict:
    """How many days of the window each IMERG run served (from ``status.json``).

    A ``product="auto"`` window is mixed by design (Late Run bulk, Early Run head); the
    consumer can see the mix instead of assuming one homogeneous run.
    """
    days = _read_status(root)["days"]
    out: dict[str, int] = {}
    for day in fields["present"]:
        run = days.get(f"{day:%Y-%m-%d}", {}).get("product") or "unknown"
        out[run] = out.get(run, 0) + 1
    return out


def load_imerg_mask(start: dt.date, end: dt.date, *, root: str | Path | None = None,
                    product: str = DEFAULT_PRODUCT) -> xr.DataArray:
    """Completeness mask for the same window/shape as :func:`load_imerg_daily` (uint8 bits).

    bit 0 (1) ``present`` — a real observation; bit 1 (2) ``full_sampling`` — all
    ``STEPS_PER_DAY`` half-hourly steps contributed (or the producer publishes no counts);
    bit 2 (4) ``likely_liquid`` — the producer's probability-of-liquid diagnostic says
    liquid *or* no precipitation. 0 = no usable observation at that cell/day.
    """
    fields = _load_fields(start, end, root=root, product=product)
    present = np.isfinite(fields["precip"])
    # an absent day has no observation anywhere: it must read as mask 0, so the sampling
    # and liquid bits are only ever set where something is actually present (the fill
    # value for unknown sampling, CNT_UNKNOWN, would otherwise light bit 1 on empty days)
    full = present & ((fields["cnt"] >= STEPS_PER_DAY) | (fields["cnt"] == CNT_UNKNOWN))
    liquid = present & (fields["pol"] >= 100)
    mask = (MASK_PRESENT * present + MASK_FULL_SAMPLING * full + MASK_LIKELY_LIQUID * liquid
            ).astype("uint8")
    return xr.DataArray(
        mask, dims=("time", "lat", "lon"), coords=_coords(fields),
        name="completeness",
        attrs={"bits": {str(k): v for k, v in MASK_BITS.items()}, "units": "1",
               "product": product, "run": run_label(product), "grid_deg": IMERG_GRID_DEG,
               "days_total": len(fields["days"]), "days_present": len(fields["present"]),
               "missing_days": [f"{d:%Y-%m-%d}" for d in fields["days"]
                                if d not in fields["present"]],
               "schema_version": SCHEMA_VERSION})


def load_imerg_status(start: dt.date, end: dt.date, *, root: str | Path | None = None,
                      product: str = DEFAULT_PRODUCT) -> pd.DataFrame:
    """Per-day completeness of the window: granule, status, sizes, global mean/max mm/day.

    ``status``: ``present`` (whole grid sampled), ``partial`` (some cells blank or
    under-sampled), ``missing`` (surveyed: no granule published), ``not_ingested``
    (nothing cached for that day yet), ``error`` (the build failed; see ``error``).
    """
    status = _read_status(root).get("days", {})
    rows = []
    for day in _daterange(start, end):
        key = f"{day:%Y-%m-%d}"
        entry = dict(status.get(key) or {})
        if not entry:
            entry["status"] = "not_ingested" if not daily_path(day, root).exists() else "present"
        entry.setdefault("product", product)
        rows.append({"day": day, **{k: entry.get(k) for k in (
            "status", "product", "granule", "available", "bytes", "mean_mm_day", "max_mm_day",
            "frac_valid", "frac_full_sampling", "cnt_min", "ingested_at", "error")}})
    return pd.DataFrame(rows).set_index("day")


# ------------------------------------------------------------------------ CLI


def _require_ewb() -> None:  # pragma: no cover - trivial
    from .._guard import require_ewb_host

    require_ewb_host()


def _days_arg(args, now: dt.datetime | None = None) -> tuple[dt.date, dt.date]:
    """--start/--end, or the last ``--days`` UTC days (inclusive of today)."""
    today = (now or dt.datetime.now(dt.timezone.utc)).date()
    if getattr(args, "start", None):
        start = dt.date.fromisoformat(args.start)
    else:
        start = today - dt.timedelta(days=args.days - 1)
    if getattr(args, "end", None):
        end = dt.date.fromisoformat(args.end)
    else:
        end = today
    return start, end


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    _require_ewb()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.sources.imerg",
                                 description="GPM IMERG daily precipitation ingest (flood type)")
    ap.add_argument("--root", default=None, help="cache root (default $EWB_LIVE_IMERG_DIR "
                                                 "or data/imerg)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("update", help="fetch granules and rebuild daily fields")
    up.add_argument("--days", type=int, default=DEFAULT_KEEP_DAYS)
    up.add_argument("--start", default=None, help="ISO date (default: today - days + 1)")
    up.add_argument("--end", default=None, help="ISO date (default: today, UTC)")
    up.add_argument("--product", default=DEFAULT_PRODUCT, choices=list(PRODUCT_KEYS),
                    help="auto = Late Run with the Early Run filling the head (default)")
    up.add_argument("--route", default=DEFAULT_ROUTE, choices=list(ROUTES),
                    help="auto = AWS Open Data S3 first, GES DISC HTTPS fallback (default)")
    up.add_argument("--workers", type=int, default=2)
    up.add_argument("--keep-raw", type=int, default=DEFAULT_KEEP_RAW)
    up.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    up.add_argument("--overwrite", action="store_true")
    up.add_argument("--min-free-bytes", type=int, default=MIN_FREE_BYTES,
                    help="refuse the fetch unless this much space stays free on the cache "
                         "filesystem after it (the ewb LXC hosts the live service)")
    up.add_argument("--batch-days", type=int, default=DEFAULT_BATCH_DAYS,
                    help="days fetched before raw/ is rolled (bounds the peak disk use)")
    up.add_argument("--dry-run", action="store_true",
                    help="survey only: what exists, how much disk it needs (no credentials)")

    st = sub.add_parser("status", help="per-day completeness of the cached window")
    st.add_argument("--days", type=int, default=DEFAULT_KEEP_DAYS)
    st.add_argument("--start", default=None)
    st.add_argument("--end", default=None)
    st.add_argument("--product", default=DEFAULT_PRODUCT, choices=list(PRODUCT_KEYS))

    ro = sub.add_parser("roll", help="drop days outside the rolling window")
    ro.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    ro.add_argument("--keep-raw", type=int, default=DEFAULT_KEEP_RAW)
    ro.add_argument("--dry-run", action="store_true")

    sub.add_parser("auth-check", help="check Earthdata credentials against one granule")

    args = ap.parse_args(argv)
    root = args.root
    log = lambda msg: print(msg, flush=True)  # noqa: E731

    if args.cmd == "update":
        start, end = _days_arg(args)
        log(f"IMERG {run_label(args.product)} {start}..{end} -> {daily_dir(root)} "
            f"(route={args.route})")
        granules, gaps = survey_window(start, end, product=args.product)
        days = _daterange(start, end)
        runs: dict[str, int] = {}
        for gran in granules.values():
            runs[gran.product] = runs.get(gran.product, 0) + 1
        proj = project_window(days, granules, batch_days=args.batch_days or DEFAULT_BATCH_DAYS)
        free = free_bytes(root)
        fits = free - proj["net"] >= args.min_free_bytes
        log(f"published granules: {len(granules)} of {len(days)} days "
            f"({runs}); gaps: {gaps or 'none'}")
        log(f"projected sizes: raw total {proj['raw_total'] / 1e9:.2f} GB "
            f"(kept: last {args.keep_raw} days), daily store "
            f"~{proj['daily_new'] / 1e9:.2f} GB for {len(granules)} days "
            f"(~12 MB/day compressed) + status.json; peak during the run "
            f"~{proj['net'] / 1e9:.2f} GB in {proj['batches']} batch(es) of {proj['batch_days']} days")
        log(f"disk: {free / 1e9:.2f} GB free on {daily_dir(root).parent}, this window needs "
            f"~{proj['net'] / 1e9:.2f} GB and {args.min_free_bytes / 1e9:.2f} GB must stay free -> "
            f"{'fits' if fits else 'WILL NOT FIT'}")
        if args.dry_run:
            if not fits:
                log("--keep-raw 0 and/or fewer --days shrink the footprint; "
                    "--min-free-bytes overrides deliberately")
            return 0
        if not fits:
            log(ImergError(f"refusing to fetch: {proj['net'] / 1e9:.2f} GB needed, only "
                           f"{free / 1e9:.2f} GB free (floor {args.min_free_bytes / 1e9:.2f} GB)"))
            return 5
        if earthdata_credentials() is None:
            log(ImergAuthError(_AUTH_STEPS))
            return 3
        summary = ingest(start, end, root=root, product=args.product, workers=args.workers,
                        keep_raw=args.keep_raw, keep_days=args.keep_days,
                        overwrite=args.overwrite, granules=granules, route=args.route,
                        batch_days=args.batch_days, min_free=args.min_free_bytes, log=log)
        log(json.dumps({k: v for k, v in summary.items() if k != "ingested"}, indent=1))
        means = [e.get("mean_mm_day") for e in summary["ingested"] if e.get("mean_mm_day")]
        if means:
            log(f"sanity: global mean {np.mean(means):.2f} mm/day (climatological expectation "
                f"~2-3), daily fields "
                f"{summary['disk']['daily_mb']} MB, raw {summary['disk']['raw_mb']} MB")
        return 0

    if args.cmd == "status":
        start, end = _days_arg(args)
        frame = load_imerg_status(start, end, root=root, product=args.product)
        print(frame.to_string())
        print(f"\n{frame['status'].value_counts().to_dict()} "
              f"| disk {json.dumps(disk_usage(root))}")
        return 0

    if args.cmd == "roll":
        out = roll_window(root=root, keep_days=args.keep_days, keep_raw=args.keep_raw,
                          dry_run=args.dry_run)
        print(json.dumps(out, indent=1))
        return 0

    # auth-check: proves the Earthdata login and reports which transport works
    creds = earthdata_credentials()
    if creds is None:
        print(ImergAuthError(_AUTH_STEPS))
        return 3
    end = dt.datetime.now(dt.timezone.utc).date()
    granules, _ = survey_window(end - dt.timedelta(days=3), end)
    if not granules:
        print("credentials found, but CMR reports no recent granules — unexpected")
        return 4
    gran = max(granules.values(), key=lambda g: g.day)
    dest = Path("/tmp") / f"imerg_auth_check_{gran.day:%Y%m%d}.nc4"
    route_used = "?"
    try:
        if s3_credentials(creds) is not None:
            _get_s3(gran.url, dest, creds, overwrite=True)
            route_used = f"s3 (AWS Open Data, {S3_BUCKET}, {S3_REGION})"
        else:
            _get_https(gran.url, dest, creds, overwrite=True)
            route_used = "https (GES DISC)"
    except (ImergError, OSError):
        _get_https(gran.url, dest, creds, overwrite=True)   # the fallback must work too
        route_used = "https (GES DISC; S3 route failed)"
    size = dest.stat().st_size
    dest.unlink(missing_ok=True)
    print(f"OK: Earthdata login works — {gran.filename} ({size} bytes) read over "
          f"{route_used}; credentials are not stored in the repo")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
