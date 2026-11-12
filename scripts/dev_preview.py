"""Local preview of dashboard/ against the deployed data (stdlib only).

    python scripts/dev_preview.py [--port 8061] [--upstream http://localhost:8050]

Serves this worktree's dashboard/ and proxies /data/* to the upstream box, so
UI work can be checked (and the e2e suite run via EWB_LIVE_URL) before a
deploy. A static file server plus a pass-through proxy — no EWB compute.
"""
import argparse
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")

# Mirrors ewb_live/serve.py OPTIONAL_FILES (kept as a literal: this helper is
# stdlib-only and must not import the package). A genuinely-absent optional
# file answers 204, not 404 — otherwise the preview console shows network
# errors the deployed server never produces (e2e asserts errs == []).
OPTIONAL = {"events_selected.json", "viz.json", "index.json", "pair_status.json",
            "scorecard_by_region.json"}


class Handler(SimpleHTTPRequestHandler):
    upstream = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if not self.path.startswith("/data/"):
            return super().do_GET()
        try:
            with urlopen(Request(self.upstream + self.path, headers={"User-Agent": "Mozilla/5.0 (preview)"}), timeout=60) as r:
                body, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/octet-stream")
        except HTTPError as e:
            body, status, ctype = b"", e.code, "text/plain"
            if status == 404 and self.path.split("?", 1)[0].rsplit("/", 1)[-1] in OPTIONAL:
                body, status, ctype = b"", 204, "text/plain"
        except Exception:
            body, status, ctype = b"", 502, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if status != 204:
            self.wfile.write(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8061)
    ap.add_argument("--upstream", default="http://localhost:8050")
    args = ap.parse_args()
    Handler.upstream = args.upstream.rstrip("/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
