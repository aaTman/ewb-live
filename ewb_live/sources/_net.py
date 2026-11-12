"""Network access helpers: anonymous S3 + CloudFront, retry, disk cache.

* ``s3fs`` anonymous clients for bucket listings and ranged object reads.
* The ECMWF ``ecmwf-forecasts`` bucket rate-limits anonymous GetObject hard
  ("SlowDown"); the AWS registry lists a CloudFront distribution
  (``d2zvc0wgha4k2l.cloudfront.net``) for public reads, so ranged object
  fetches for that bucket go through CloudFront while LIST (which CloudFront
  does not serve) still goes to S3 with retries.
* A small in-process TTL cache for directory listings keeps interactive use
  snappy.
"""

from __future__ import annotations

import http.client
import os
import random
import threading
import time
import urllib.error
import urllib.request

import s3fs
from fsspec.implementations.http import HTTPFileSystem

ECMWF_BUCKET = "ecmwf-forecasts"
ECMWF_CLOUDFRONT = "https://d2zvc0wgha4k2l.cloudfront.net"

AIWP_BUCKET = "noaa-oar-mlwp-data"
AIGFS_BUCKET = "noaa-nws-graphcastgfs-pds"

CACHE_ROOT = os.environ.get(
    "EWB_LIVE_CACHE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "cache"),
)

_s3_lock = threading.Lock()
_s3_clients: dict[str, s3fs.S3FileSystem] = {}
_listings: dict[str, tuple[float, list]] = {}
_LIST_TTL = float(os.environ.get("EWB_LIVE_LIST_TTL", 300))

# Network read/connect timeouts (stall postmortem 2026-11-07): a wedged
# socket must fail the job fast into the retry/timeout path instead of
# masquerading as a very slow compute job. botocore's default read timeout
# applies per socket read and does not cap a stalled stream.
S3_CONNECT_TIMEOUT_S = float(os.environ.get("EWB_LIVE_S3_CONNECT_TIMEOUT_S", "15"))
S3_READ_TIMEOUT_S = float(os.environ.get("EWB_LIVE_S3_READ_TIMEOUT_S", "120"))


def _botocore_config():
    # s3fs>=2026.9 builds AioConfig(**config_kwargs) itself; a botocore
    # Config object inside client_kwargs collides with that in
    # aiobotocore>=3.7 (create_client() multiple 'config' — D1, 2026-11-08).
    # config_kwargs is the sanctioned channel and carries the same options.
    return dict(connect_timeout=S3_CONNECT_TIMEOUT_S, read_timeout=S3_READ_TIMEOUT_S,
                retries={"max_attempts": 3, "mode": "standard"})


def s3(region: str = "us-east-1") -> s3fs.S3FileSystem:
    with _s3_lock:
        if region not in _s3_clients:
            _s3_clients[region] = s3fs.S3FileSystem(
                anon=True, config_kwargs=_botocore_config(),
                client_kwargs={"region_name": region},
            )
        return _s3_clients[region]


def with_retries(fn, tries: int = 6, base_delay: float = 1.0):
    """Run ``fn`` retrying on S3 throttling / transient HTTP errors."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except (
            OSError,
            IOError,
            urllib.error.HTTPError,
            http.client.HTTPException,
        ) as exc:  # noqa: BLE001
            # 429 = the actual S3/CloudFront throttle status (ponytail 2026-11-08).
            # HTTPError subclasses OSError, so it must be branched first or a
            # definitive 404/403 burns the whole backoff ladder (ponytail 22:21).
            if isinstance(exc, urllib.error.HTTPError):
                throttled = exc.code in (429, 500, 502, 503, 504)
            elif isinstance(exc, (FileNotFoundError, PermissionError)):
                # s3fs surfaces a definitive 404/403 as these OSError
                # subclasses — same class as the HTTPError-404 fix, on the
                # s3 path (ponytail 2026-11-08 23:42)
                raise
            else:
                throttled = (isinstance(exc, OSError)
                             or isinstance(exc, http.client.HTTPException))
            last = exc
            if not throttled or attempt == tries - 1:
                raise
            time.sleep(base_delay * (attempt + 1) + random.uniform(0, 0.5))
    raise RuntimeError(str(last))  # pragma: no cover


def list_prefix(bucket: str, prefix: str, region: str = "us-east-1", suffix: str | None = None) -> list[str]:
    """List keys under ``prefix`` (with delimiter semantics done client-side).

    Returns full keys (bucket/prefix/key). Cached in-process for _LIST_TTL.
    """
    key = f"{bucket}/{prefix}|{suffix}"
    now = time.time()
    hit = _listings.get(key)
    if hit and now - hit[0] < _LIST_TTL:
        return hit[1]
    fs = s3(region)
    path = f"{bucket}/{prefix}"

    def _ls():
        if path.endswith("/") or prefix == "":
            entries = fs.ls(path, detail=False)
        else:
            # path might be a file
            try:
                info = fs.info(path)
                return [path] if info.get("type") == "file" else fs.ls(path, detail=False)
            except FileNotFoundError:
                return []
        return entries

    out = with_retries(_ls)
    if suffix:
        out = [k for k in out if k.endswith(suffix)]
    _listings[key] = (now, out)
    return out


def fetch_range_http(url: str, start: int | None = None, stop: int | None = None,
                     timeout: float = 90.0) -> bytes:
    """GET over plain HTTPS (CloudFront), optionally a byte range, with retries."""

    def _get():
        headers = {} if start is None or stop is None else {"Range": f"bytes={start}-{stop - 1}"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    return with_retries(_get)


def open_remote_nc(s3_path: str, region: str = "us-east-1"):
    """Open a remote NetCDF4 file for lazy (range-request) access."""
    import fsspec

    # config via config_kwargs, NOT client_kwargs: with s3fs 2026.9.0 /
    # aiobotocore 3.10.0 passing both region and config inside
    # client_kwargs raises "create_client() got multiple values for
    # keyword argument 'config'" (ponytail MUST 2026-11-08 23:42; same
    # D1 collision as the s3() fix)
    return fsspec.open(s3_path, mode="rb", anon=True,
                       client_kwargs={"region_name": region},
                       config_kwargs=_botocore_config()).open()


def cache_path(*parts: str) -> str:
    p = os.path.join(CACHE_ROOT, *parts)
    os.makedirs(p, exist_ok=True)
    return p
