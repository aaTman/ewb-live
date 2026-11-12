"""Static server for the EWB Live dashboard (stdlib only — no dependencies).

Run from anywhere:

    python -m ewb_live.serve --port 8050 [--host 0.0.0.0]

Web root is `dashboard/`; the repo root is exposed at /data/results/ only:

  /                       -> dashboard/index.html (+ dashboard assets)
  /data/results/*.json    -> pipeline outputs, with automatic fallback to
                             data/results/sample/<name> when a real file is
                             missing, so the dashboard works before the first
                             pipeline run and during partial population.

Nothing else on disk is reachable through the server. JSON responses are sent
with no-cache headers so a daily pipeline update is visible on the next
browser refresh.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")
RESULTS_DIR = os.path.join(REPO_ROOT, "data", "results")
SAMPLE_DIR = os.path.join(RESULTS_DIR, "sample")
HISTORY_DIR = os.path.join(RESULTS_DIR, "history")

WEEK_RE = re.compile(r"^[0-9]{4}-W[0-9]{2}$")
WEEKS_ROUTE = "/data/results/weeks.json"

# Files the dashboard probes for (optional, presence carries meaning). When
# absent we answer 204 No Content instead of 404 so the browser's console
# stays clean for a deliberately error-gated dashboard. viz.json / index.json
# apply to nested paths under data/results/events/ (per-event viz assets).
OPTIONAL_FILES = {"events_selected.json", "viz.json", "index.json", "pair_status.json",
                  "scorecard_by_region.json"}

# Segment whitelist for nested results paths (event ids are hex-ish; keep it
# boring anyway). normpath() has already collapsed "." / ".." by validation
# time, so this is about shape, not jail (the jail is the ".." stripping above).
_SAFE_SEG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Frozen review snapshots under data/results/<dir>/ (owner decision 2026-11-06:
# dashboard `?data=mini` exercises all views while the real run finishes).
MINI_DIRS = ("mini", "mini-sparse", "mini-nofreeze", "mini-am")


def _is_safe_nested_json(segs: list[str]) -> bool:
    """True for data/results/events/.../<file>.json with only safe segments."""
    return (len(segs) >= 2 and segs[0] == "events"
            and all(_SAFE_SEG_RE.match(s) for s in segs)
            and segs[-1].endswith(".json"))


def weeks_listing() -> dict:
    """Weeks available under data/results/history/ (weekly snapshots)."""
    weeks = []
    if os.path.isdir(HISTORY_DIR):
        for name in sorted(os.listdir(HISTORY_DIR)):
            d = os.path.join(HISTORY_DIR, name)
            if not os.path.isdir(d) or not WEEK_RE.match(name):
                continue
            files = sorted(f for f in os.listdir(d)
                           if f.endswith(".json") and os.path.isfile(os.path.join(d, f)))
            if files:
                weeks.append({"week": name, "files": files})
    return {"weeks": weeks}

# Public base URL (e.g. https://example.org), injected into index.html's
# canonical / og:url / og:image so social cards get absolute links. Unset ->
# those tags are dropped (the page stays valid; nothing host-specific is
# baked into the repo).
PUBLIC_URL = os.environ.get("EWB_PUBLIC_URL", "").rstrip("/")

# Defence in depth for the public site (also set by the nginx front when it is
# in the path). Scripts are all same-origin vendor files; 'unsafe-inline' for
# script-src is required because the CDN edge injects an inline bot-detection
# snippet into every HTML response, and the edge's Web Analytics beacon
# (static.cloudflareinsights.com) is allowed so it doesn't log a violation —
# disable that in the CDN dashboard to drop the third party entirely. Everything that could exfiltrate
# (connect/img/frame/form/base) stays pinned to this origin + the basemap host.
CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https://server.arcgisonline.com https://tile.openstreetmap.org; "
       "font-src 'self'; connect-src 'self' https://cloudflareinsights.com; worker-src 'self' blob:; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    ("Content-Security-Policy", CSP),
)

NOT_FOUND_PAGE = ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
                  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                  "<meta name=\"robots\" content=\"noindex\"><title>Not found — EWB Live</title>"
                  "<body style=\"font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem\">"
                  "<h1 style=\"font-size:1.25rem\">Page not found</h1>"
                  "<p>That page or data file does not exist. <a href=\"/\">Back to EWB Live</a></p></body></html>")

# web-root file types that may be served (directory listings are disabled).
# `.py` is here for one file: dashboard/detection.py, the criteria listing the
# About dialog shows and offers for download. The web root is dashboard/ only
# (its JavaScript is already public source), so this adds no new surface — but
# keep it that way: nothing that does not belong on the site goes in dashboard/.
STATIC_EXT = {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico",
              ".woff2", ".woff", ".json", ".geojson", ".txt", ".webmanifest", ".py"}

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves dashboard/ at /, plus data/results/ mapped under /data/results/."""

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        parts = posixpath.normpath(unquote(path)).split("/")
        parts = [p for p in parts if p not in ("", "..", ".")]
        if not parts:
            return DASHBOARD_DIR
        if parts[0] == "data":
            rest = parts[1:]
            if rest[:1] == ["results"]:
                sub = rest[1:]  # path relative to data/results/
                if len(sub) == 1 and sub[0].endswith(".json") and not sub[0].startswith("."):
                    # top-level results files: JSON ONLY — the dir also holds
                    # dev logs/parquet which must never be public (scrub rule)
                    real = os.path.join(RESULTS_DIR, sub[0])
                    if os.path.isfile(real):
                        return real
                    fallback = os.path.join(SAMPLE_DIR, sub[0])
                    if os.path.isfile(fallback):
                        self._sample_hit = sub[0]  # for X-EWB-Data header
                        return fallback
                    return real  # missing -> normal 404
                if (len(sub) == 2 and sub[0] == "sample" and sub[1].endswith(".json")
                        and not sub[1].startswith(".")):  # explicit sample path (client fallback)
                    return os.path.join(SAMPLE_DIR, sub[1])
                if (len(sub) == 2 and sub[0] in MINI_DIRS and sub[1].endswith(".json")
                        and not sub[1].startswith(".")):
                    # mini snapshot dirs (?data=mini): frozen copies + synthetic
                    # cells written by scripts/build_mini_results.py. No sample
                    # fallback — an explicit mini request 404s rather than
                    # silently serving sample data.
                    real = os.path.join(RESULTS_DIR, *sub)
                    return real
                if (len(sub) >= 3 and sub[0] in MINI_DIRS
                        and _is_safe_nested_json(sub[1:])):
                    # nested JSON inside a frozen snapshot dir (e.g.
                    # mini-am/events/<id>/viz.json QA fixtures) — same shape
                    # rules as the live events tree, no sample fallback.
                    return os.path.join(RESULTS_DIR, *sub)
                if (len(sub) == 3 and sub[0] == "history" and WEEK_RE.match(sub[1])
                        and sub[2].endswith(".json") and "." not in sub[2][:-5]):
                    # weekly snapshot: history/<YYYY-WW>/<file>.json (no sample fallback)
                    return os.path.join(HISTORY_DIR, sub[1], sub[2])
                if (len(sub) == 2 and sub[0] == "raw_exceedance"
                        and re.fullmatch(r"\d{10}\.(?:bin|trk|run|ivt)|index\.json", sub[1])):
                    # raw 6-hourly exceedance frames (design lane): exact
                    # YYYYMMDDHH.bin/.trk/.run/.ivt or index.json only — strict
                    # allowlist, no listing. Bodies are pre-zlib'd; served
                    # octet-stream.
                    return os.path.join(RESULTS_DIR, *sub)
                if _is_safe_nested_json(sub):
                    # per-event viz assets: events/<event_id>/viz.json, events/index.json
                    # (no sample fallback; missing -> 404 or 204 via OPTIONAL_FILES)
                    return os.path.join(RESULTS_DIR, *sub)
                return os.path.join(REPO_ROOT, "nonesuch")  # dirs/subpaths: no listing
            return os.path.join(REPO_ROOT, "nonesuch")  # data/* except results
        if parts[0] == "dashboard":  # tolerate the legacy /dashboard/ prefix
            parts = parts[1:]
        # static allowlist: only web asset types, never dotfiles, dev tooling
        # (dashboard/tools/*.py) or caches that happen to live in the web root
        if parts and (parts[0] == "tools" or any(p.startswith(".") or p == "__pycache__" for p in parts)
                      or os.path.splitext(parts[-1])[1].lower() not in STATIC_EXT):
            return os.path.join(REPO_ROOT, "nonesuch")
        return os.path.join(DASHBOARD_DIR, *parts)

    def list_directory(self, path):
        self.send_error(404)   # no directory listings (vendor/, geo/, ...)
        return None

    server_version = "ewb-live"   # no Python/OS version in the Server header
    sys_version = ""

    def version_string(self):
        return self.server_version

    def send_error(self, code, message=None, explain=None):
        if code != 404:
            return super().send_error(code, message, explain)
        body = NOT_FOUND_PAGE.encode("utf-8")
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # a not-yet-built file must not be cached as missing
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_index(self) -> bool:
        """index.html with the public-URL tokens resolved (or their tags dropped)."""
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/index.html"):
            return False
        try:
            with open(os.path.join(DASHBOARD_DIR, "index.html"), encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            return False
        if PUBLIC_URL:
            html = html.replace("%PUBLIC_URL%", PUBLIC_URL)
        else:
            html = "".join(ln for ln in html.splitlines(keepends=True) if "%PUBLIC_URL%" not in ln)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return True

    def do_HEAD(self):  # noqa: N802
        if self._serve_index():
            return
        super().do_HEAD()

    def do_GET(self):  # noqa: N802
        if self._serve_index():
            return
        if self.path.split("?", 1)[0] == WEEKS_ROUTE:
            body = json.dumps(weeks_listing()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?", 1)[0].split("/")[-1] in OPTIONAL_FILES \
                and "/data/results/" in self.path:
            # optional probe file: 204 instead of 404 when genuinely missing
            if not os.path.isfile(self.translate_path(self.path)):
                self.send_response(204)
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                return
        if self._try_gzip():   # DR2 M1: gzip JSON/JS/CSS (~5-10x on scores.json)
            return
        super().do_GET()

    # ---- gzip (M1, DR2): compress text assets on the fly, mtime-keyed cache
    GZIP_EXT = (".json", ".js", ".css", ".svg")
    _gzip_cache: dict = {}

    def _try_gzip(self) -> bool:
        import gzip
        path = self.path.split("?", 1)[0]
        if not path.endswith(self.GZIP_EXT):
            return False
        if "gzip" not in self.headers.get("Accept-Encoding", "").lower():
            return False
        fs = self.translate_path(self.path)
        if not os.path.isfile(fs):
            return False
        try:
            mtime = os.path.getmtime(fs)
        except OSError:
            return False
        if path.endswith(".json") and not os.path.basename(fs).endswith(".json"):
            return False   # sample-fallback mismatch: don't serve the wrong file
        key = (fs, mtime)
        hit = self._gzip_cache.get(key)
        if hit is None:
            with open(fs, "rb") as fh:
                raw = fh.read()
            if len(raw) < 1024:
                return False
            data = gzip.compress(raw, 6, mtime=0)
            # drop stale mtimes of the same path first: every publish rewrites
            # scores.json, and 48 old blobs of a growing file is the wrong cap
            for k in [k for k in self._gzip_cache if k[0] == fs]:
                del self._gzip_cache[k]
            if len(self._gzip_cache) > 48:
                self._gzip_cache.clear()
            ctype = mimetypes.guess_type(fs)[0] or "application/octet-stream"
            hit = (data, ctype)
            self._gzip_cache[key] = hit
        data, ctype = hit
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass   # client gone mid-write
        return True

    def end_headers(self):
        path = self.path.split("?", 1)[0]
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        if getattr(self, "_sample_hit", None) and path.rstrip("/").endswith(self._sample_hit):
            self.send_header("X-EWB-Data", "sample")  # served via sample fallback
        # a handler (weeks route, 204 probes) may already have set its own policy
        has_cc = any(h.lower().startswith(b"cache-control:")
                     for h in getattr(self, "_headers_buffer", ()))
        top = path.count("/") <= 1   # "/", "/app.js", "/index.html"... (not vendor/, geo/, data/)
        if has_cc:
            pass
        elif path.endswith(".json"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        elif top and (path == "/" or path.endswith((".html", ".js", ".css"))):
            # our OWN top-level assets (app.js, eventviz.js, mergedmap.js,
            # styles.css, index.html): always revalidate so a deploy can never
            # leave a new app.js talking to a stale sibling script (vendor/ is cached)
            self.send_header("Cache-Control", "no-cache")
        elif path.startswith("/vendor/fonts/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif path.startswith(("/vendor/", "/geo/")):
            self.send_header("Cache-Control", "public, max-age=604800")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        super().end_headers()


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m ewb_live.serve",
                                 description="Serve the EWB Live dashboard.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8050)
    args = ap.parse_args()

    if not os.path.isdir(RESULTS_DIR):
        print(f"note: {RESULTS_DIR} does not exist yet; sample fallback also unavailable")

    ThreadingHTTPServer.allow_reuse_address = True
    handler = lambda *a, **kw: DashboardHandler(*a, directory=DASHBOARD_DIR, **kw)  # noqa: E731
    with ThreadingHTTPServer((args.host, args.port), handler) as httpd:
        shown = "localhost" if args.host in ("0.0.0.0", "") else args.host
        print(f"EWB Live dashboard: http://{shown}:{args.port}/  (dashboard: {DASHBOARD_DIR})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
