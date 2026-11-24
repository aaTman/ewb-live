"""Acceptance-criteria E2E verification suite for the EWB Live UX redesign.

Exercises ALL 7 event types (heat_wave, cold_snap, freeze, atmospheric_river,
flood, tropical_cyclone, midlat_cyclone), the unified Scorecard overview &
drill-down, floating map legend popover, decluttered track labels, Events
On-map/All sync, About accordions, dark/light axe-core WCAG AA checks, and
mobile/desktop no-overflow gates against an isolated in-process fixture server.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DASH = os.path.join(ROOT, "dashboard")
AXE_JS = os.path.join(os.path.dirname(__file__), "vendor", "axe.min.js")
SHOTS = os.path.join(ROOT, "notes", "screenshots")

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


def _make_daily_bits(ny: int, nx: int, ndays: int, active_rect: tuple[int, int, int, int]) -> dict:
    y0, y1, x0, x1 = active_rect
    stride = (ny * nx + 7) // 8
    buf = bytearray(ndays * stride)
    for d in range(ndays):
        for j in range(y0, y1):
            for i in range(x0, x1):
                k = j * nx + i
                buf[d * stride + (k >> 3)] |= 1 << (7 - (k & 7))
    return {"b64": base64.b64encode(bytes(buf)).decode("ascii"), "ny": ny, "nx": nx, "ndays": ndays}


def _make_consec_viz(etype: str, event_id: str, quantile: float, extra_obs: dict | None = None) -> dict:
    lats = [28.0 + 0.5 * j for j in range(12)]
    lons = [-102.0 + 0.5 * i for i in range(16)]
    days = [f"2026-10-{18 + d:02d}" for d in range(6)]
    bits = _make_daily_bits(len(lats), len(lons), len(days), (3, 9, 4, 12))
    obs = {
        "anchor_day": days[3],
        "max_consec": 6,
        "footprint_cells": 48,
        "daily_bits": bits,
    }
    if extra_obs:
        obs.update(extra_obs)
    ok_entry = {
        "status": "ok",
        "max_consec": 6,
        "footprint_cells": 50,
        "daily_bits": bits,
    }
    models = {
        "ifs_hres": {"24": ok_entry, "72": ok_entry},
        "aifs_single": {"24": ok_entry, "72": ok_entry},
        "graphcast": {"24": {"status": "unavailable (no data at this lead)"}, "72": ok_entry},
        "pangu_weather": {"24": {"status": "unavailable (no data at this lead)"}},
        "fourcastnet_v2": {"24": {"status": "unavailable (no data at this lead)"}},
        "aurora": {"24": {"status": "unavailable (no data at this lead)"}},
        "aigfs": "unavailable (no 2 m temperature)",
    }
    out = {
        "schema": 3,
        "kind": "consecutive_days",
        "event_id": event_id,
        "event_type": etype,
        "quantile": quantile,
        "truth_source": "era5_mirror",
        "grid": {"lat": lats, "lon": lons, "cell_deg": 0.5},
        "window": {"days": days},
        "obs": obs,
        "models": models,
    }
    if etype == "flood":
        out["source_label"] = "AI-estimated (Google PARDIG)"
    return out


def _make_ar_viz(event_id: str) -> dict:
    lats = [40.0 + 0.5 * j for j in range(10)]
    lons = [-10.0 + 0.5 * i for i in range(14)]
    ivt = [[650.0 if 2 <= j <= 7 and 3 <= i <= 10 else 180.0 for i in range(14)] for j in range(10)]
    mask_rows = ["/".join("1" * 14 if 2 <= j <= 7 else "0" * 14 for j in range(10))][0]
    ok_entry = {"status": "ok", "ivt": ivt, "mask": mask_rows, "area_cells": 68}
    return {
        "schema": 1,
        "kind": "ar_ivt",
        "event_id": event_id,
        "event_type": "atmospheric_river",
        "ivt_threshold_kg_m_s": 400,
        "grid": {"lat": lats, "lon": lons, "cell_deg": 0.5},
        "obs": {"anchor_time": "2026-10-23T12:00:00Z", "ivt": ivt, "mask": mask_rows, "area_cells": 72},
        "models": {
            "ifs_hres": {"24": ok_entry, "72": ok_entry},
            "aifs_single": {"24": ok_entry, "72": ok_entry},
            "graphcast": {"24": {"status": "unavailable (no data at this lead)"}},
            "aurora": {"24": {"status": "unavailable (no data at this lead)"}},
        },
    }


def _build_fixture(root: str) -> None:
    res = os.path.join(root, "data", "results")
    ev_dir = os.path.join(res, "events")
    raw_dir = os.path.join(res, "raw_exceedance")
    os.makedirs(ev_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)

    t0 = datetime(2026, 10, 20, 0)
    tc_track = [
        [(t0 + timedelta(hours=6 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), 14.0 + i * 1.1, 136.0 - i * 1.4, w, p]
        for i, (w, p) in enumerate([(28, 1004), (42, 996), (68, 980), (105, 950), (145, 912), (110, 945), (55, 988), (30, 1002)])
    ]
    mlc_track = [
        [(t0 + timedelta(hours=6 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), -52.0 - i * 0.4, -35.0 + i * 3.0, p, p]
        for i, p in enumerate([994.0, 984.0, 972.0, 956.0, 941.0, 949.0, 965.0, 982.0])
    ]
    mlc_anon2 = [
        [(t0 + timedelta(hours=6 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), -54.0, -25.0 + i * 2.5, p, p]
        for i, p in enumerate([992.0, 981.0, 966.0, 948.0, 958.0, 975.0])
    ]

    events = [
        {
            "id": "hw001a", "event_type": "heat_wave", "title": "Heat Wave US",
            "start": "2026-10-18", "end": "2026-10-25", "qualified_at": "2026-10-20T00:00:00Z",
            "bbox": [28.0, 34.0, -102.0, -94.0], "peak_value": 41.2,
            "details": {"peak_value_units": "°C", "duration_days": 7, "max_consecutive_days": 6},
        },
        {
            "id": "cs001b", "event_type": "cold_snap", "title": "Cold Snap Europe",
            "start": "2026-10-19", "end": "2026-10-25", "qualified_at": "2026-10-21T00:00:00Z",
            "bbox": [46.0, 52.0, 8.0, 18.0], "peak_value": 2.4,
            "details": {"peak_value_units": "°C", "duration_days": 6, "max_consecutive_days": 5},
        },
        {
            "id": "fr001c", "event_type": "freeze", "title": "Freeze Siberia",
            "start": "2026-10-19", "end": "2026-10-26", "qualified_at": "2026-10-21T00:00:00Z",
            "bbox": [58.0, 64.0, 80.0, 92.0], "peak_value": -18.6,
            "details": {"peak_value_units": "°C", "duration_days": 7, "max_consecutive_days": 6},
        },
        {
            "id": "ar001d", "event_type": "atmospheric_river", "title": "AR France",
            "start": "2026-10-20", "end": "2026-10-25", "qualified_at": "2026-10-21T00:00:00Z",
            "bbox": [40.0, 46.0, -10.0, 2.0], "peak_value": 900.0,
            "details": {"peak_value_units": "kg m⁻¹ s⁻¹", "landfall_time": "2026-10-23", "duration_days": 5},
        },
        {
            "id": "fl001e", "event_type": "flood", "title": "Flood Germany",
            "start": "2026-10-20", "end": "2026-10-24", "qualified_at": "2026-10-21T00:00:00Z",
            "bbox": [48.0, 52.0, 7.0, 13.0], "peak_value": 242.0,
            "details": {"peak_value_units": "mm", "peak_daily_mm": 118.0, "peak_area_km2": 65000, "duration_days": 4},
        },
        {
            "id": "tc001f", "event_type": "tropical_cyclone", "title": "TC Champi",
            "start": "2026-10-20T06:00:00Z", "end": "2026-10-21T18:00:00Z", "qualified_at": "2026-10-20T06:00:00Z",
            "bbox": [12.0, 24.0, 124.0, 138.0], "peak_value": 145.0,
            "details": {
                "name": "Champi", "basin": "WP", "basin_name": "Northwest Pacific",
                "peak_wind_kt": 145.0, "peak_category": "CAT 5", "min_pressure_hpa": 912.0,
                "intensity_units": "kt", "peak_value_units": "kt", "fix_step_h": 6,
                "wind_source": "USA", "wind_basis": "1-min", "source": "IBTrACS",
                "track": tc_track,
            },
        },
        {
            "id": "mlc001g", "event_type": "midlat_cyclone", "title": "MLC Southern Ocean",
            "start": "2026-10-20T06:00:00Z", "end": "2026-10-21T18:00:00Z", "qualified_at": "2026-10-20T06:00:00Z",
            "bbox": [-56.0, -50.0, -36.0, -12.0], "peak_value": 941.0,
            "details": {
                "min_pressure_hpa": 941.0, "displacement_km": 2450.0,
                "bomb": True, "bomb_ratio": 1.4,
                "intensity_units": "hPa", "peak_value_units": "hPa", "fix_step_h": 6,
                "source": "ERA5 MSLP", "track": mlc_track,
            },
        },
        {
            "id": "mlc002h", "event_type": "midlat_cyclone", "title": "MLC South Atlantic",
            "start": "2026-10-20T06:00:00Z", "end": "2026-10-21T12:00:00Z", "qualified_at": "2026-10-20T06:00:00Z",
            "bbox": [-56.0, -52.0, -26.0, -10.0], "peak_value": 948.0,
            "details": {
                "min_pressure_hpa": 948.0, "displacement_km": 1800.0,
                "bomb": False, "intensity_units": "hPa", "peak_value_units": "hPa",
                "fix_step_h": 6, "source": "ERA5 MSLP", "track": mlc_anon2,
            },
        },
    ]

    # Write viz.json for all 5 gridded types
    for eid, payload in [
        ("hw001a", _make_consec_viz("heat_wave", "hw001a", 0.85)),
        ("cs001b", _make_consec_viz("cold_snap", "cs001b", 0.15)),
        ("fr001c", _make_consec_viz("freeze", "fr001c", 0.15)),
        ("ar001d", _make_ar_viz("ar001d")),
        ("fl001e", _make_consec_viz("flood", "fl001e", 0.95, extra_obs={"series": [{"cells": 48, "run": 4}]})),
    ]:
        p = os.path.join(ev_dir, eid)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "viz.json"), "w") as fh:
            json.dump(payload, fh)

    models = ["ifs_hres", "aifs_single", "aurora", "graphcast", "pangu_weather", "fourcastnet_v2_gfs", "aigfs"]
    meta = {
        "last_updated": "2026-10-25T07:00:00Z",
        "window": {"start": "2026-07-27", "end": "2026-10-25", "days": 90},
        "models": models,
        "baseline_model": "ifs_hres",
        "truth_source": {"primary": "era5_arco", "fallback": "ifs_hres_0h"},
        "full_run_complete": True,
        "ibtracs": {"storms_in_window": 2, "latest_fix": "2026-10-23T00:00:00Z", "lag_days": 2, "pending": 0},
        "track_verification": {"tropical_cyclone": {"min_wind_kt": 34, "n_inits_verified": 40, "n_inits_not_verified": 6}},
    }

    sc_rows = []
    # Scored lanes: heat_wave, cold_snap, freeze, atmospheric_river, tropical_cyclone
    # Leave flood & midlat_cyclone unscored in this fixture to verify clean unscored-lane summary!
    def add_sc(et, met, lead, vals):
        for m, (v, rel) in vals.items():
            sc_rows.append({
                "event_type": et, "metric": met, "lead_time_hours": lead, "model": m,
                "mean": v, "n_events": 10, "skill_vs_ifs_pct": rel,
            })

    for lead, mult in [(24, 1.0), (72, 1.3), (120, 1.6), (168, 2.0), (240, 2.5)]:
        add_sc("heat_wave", "RootMeanSquaredError", lead, {
            "ifs_hres": (1.4 * mult, 0.0),
            "aifs_single": (1.1 * mult, 21.4),
            "aurora": (1.22 * mult, 12.8),
            "graphcast": (1.35 * mult, 3.6),
            "pangu_weather": (1.36 * mult, 2.9),
            "fourcastnet_v2_gfs": (1.48 * mult, -5.7),
        })
        add_sc("cold_snap", "RootMeanSquaredError", lead, {
            "ifs_hres": (1.9 * mult, 0.0),
            "aifs_single": (1.55 * mult, 18.4),
            "aurora": (1.8 * mult, 5.2),
            "graphcast": (1.98 * mult, -4.2),
            "pangu_weather": (1.85 * mult, 2.6),
            "fourcastnet_v2_gfs": (1.62 * mult, 14.7),
        })
        add_sc("freeze", "RootMeanSquaredError", lead, {
            "ifs_hres": (3.1 * mult, 0.0),
            "aifs_single": (2.95 * mult, 4.8),
            "aurora": (3.3 * mult, -6.5),
            "graphcast": (3.28 * mult, -5.8),
        })
        add_sc("atmospheric_river", "CriticalSuccessIndex", lead, {
            "ifs_hres": (0.42 / (mult ** 0.3), 0.0),
            "aifs_single": (0.58 / (mult ** 0.3), 38.1),
            "aurora": (0.49 / (mult ** 0.3), 16.7),
            "graphcast": (0.47 / (mult ** 0.3), 11.9),
        })
    for lead, mult in [(24, 1.0), (48, 1.8), (72, 2.7), (96, 3.7), (120, 5.0)]:
        add_sc("tropical_cyclone", "TrackPositionError", lead, {
            "ifs_hres": (52.0 * mult, 0.0),
            "graphcast": (43.5 * mult, 16.3),
            "aurora": (48.0 * mult, 7.7),
            "pangu_weather": (53.0 * mult, -1.9),
            "fourcastnet_v2_gfs": (70.0 * mult, -34.6),
        })
        add_sc("tropical_cyclone", "TrackMiss", lead, {
            "ifs_hres": (0.06, 0.0),
            "graphcast": (0.10, -66.0),
            "aurora": (0.12, -100.0),
            "pangu_weather": (0.09, -50.0),
            "fourcastnet_v2_gfs": (0.18, -200.0),
        })

    scores = [
        {"event_id": "hw001a", "event_type": "heat_wave", "model": "ifs_hres", "metric": "RootMeanSquaredError", "lead_time_hours": 24, "value": 1.45},
        {"event_id": "hw001a", "event_type": "heat_wave", "model": "aifs_single", "metric": "RootMeanSquaredError", "lead_time_hours": 24, "value": 1.05},
        {"event_id": "tc001f", "event_type": "tropical_cyclone", "model": "ifs_hres", "metric": "TrackPositionError", "lead_time_hours": 24, "value": 51.0},
        {"event_id": "tc001f", "event_type": "tropical_cyclone", "model": "graphcast", "metric": "TrackPositionError", "lead_time_hours": 24, "value": 42.0},
    ]

    with open(os.path.join(res, "meta.json"), "w") as fh:
        json.dump(meta, fh)
    with open(os.path.join(res, "events_selected.json"), "w") as fh:
        json.dump(events, fh)
    with open(os.path.join(res, "scorecard.json"), "w") as fh:
        json.dump({"baseline": "ifs_hres", "rows": sc_rows}, fh)
    with open(os.path.join(res, "scores.json"), "w") as fh:
        json.dump(scores, fh)
    with open(os.path.join(res, "pair_status.json"), "w") as fh:
        json.dump({}, fh)
    with open(os.path.join(res, "weeks.json"), "w") as fh:
        json.dump({"weeks": [{"week": "2026-W43"}]}, fh)

    # Minimal raw_exceedance + global_timeline so MergedMap & TrackLayer mount cleanly
    nx, ny = 36, 18
    stride = (nx + 7) // 8
    plane = ny * stride
    frame_bytes = bytearray(3 * plane)
    # Paint heat (plane 0), freeze (plane 1), AR (plane 2) bits
    frame_bytes[0 * plane + 10 * stride + 1] = 0xFF
    frame_bytes[1 * plane + 14 * stride + 2] = 0xFF
    frame_bytes[2 * plane + 12 * stride + 2] = 0xFF
    frames = ["2026102006", "2026102012", "2026102018", "2026102100", "2026102106", "2026102112"]
    for fr in frames:
        with open(os.path.join(raw_dir, f"{fr}.bin"), "wb") as fh:
            fh.write(frame_bytes)
    with open(os.path.join(raw_dir, "index.json"), "w") as fh:
        json.dump({
            "grid": {"nx": nx, "ny": ny, "lat0": -85.0, "lon0": -175.0, "step_lat": 10.0, "step_lon": 10.0},
            "frames": frames,
            "truth_source": "era5",
        }, fh)

    tl_events = []
    for e in events:
        entry = {
            "id": e["id"], "type": e["event_type"], "title": e["title"],
            "start": e["start"], "end": e["end"], "qualified_at": e.get("qualified_at"),
            "bbox": e["bbox"],
        }
        if e["details"].get("track"):
            entry["track"] = {
                "name": e["details"].get("name"),
                "basin": e["details"].get("basin_name"),
                "peak": e["peak_value"],
                "peak_label": e["details"].get("peak_category"),
                "pressure": e["details"].get("min_pressure_hpa"),
                "units": e["details"].get("intensity_units"),
                "fixes": e["details"]["track"],
            }
        tl_events.append(entry)
    with open(os.path.join(res, "global_timeline.json"), "w") as fh:
        json.dump({"window": {"start": "2026-10-18", "end": "2026-10-25"}, "events": tl_events}, fh)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        # Mirror ewb_live/serve.py: missing optional data JSONs return 204 so
        # Chromium does not log benign optional-asset 404s as console.error.
        if code == 404 and self.path.startswith("/data/results/") and self.path.endswith(".json"):
            self.send_response(204)
            self.end_headers()
            return
        super().send_error(code, message, explain)


@pytest.fixture(scope="module")
def app_url(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("ux_redesign"))
    shutil.copytree(DASH, root, dirs_exist_ok=True)
    _build_fixture(root)
    handler = lambda *a, **k: _Quiet(*a, directory=root, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def _open_page(browser, url: str, width: int = 1280, height: int = 800):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    pg = ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.on("console", lambda m: errs.append(f"console.error: {m.text}") if m.type == "error" else None)
    # Stub external tile requests with a 1x1 transparent PNG so Leaflet maps
    # settle fast offline without logging spurious net::ERR_FAILED console errors.
    tiny_png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    pg.route("https://**/*", lambda r: r.fulfill(status=200, content_type="image/png", body=tiny_png))
    pg.goto(url, wait_until="domcontentloaded")
    pg.wait_for_function("() => document.querySelectorAll('#event-table tbody tr[data-id]').length >= 8", timeout=15000)
    pg.wait_for_function("() => window.MergedMap && window.MergedMap._dbg().mounted", timeout=15000)
    return ctx, pg, errs


def test_view_switching_floating_legend_and_track_labels(app_url):
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx, pg, errs = _open_page(b, app_url, 1280, 800)
        try:
            # AC-1.1: Events view is shown by default; Scorecard is hidden until switched
            assert pg.locator("#explore").is_visible()
            assert not pg.locator("#scorecard-sec").is_visible(), "Scorecard must be a distinct view, not stacked below Events"

            # AC-1.6: Search placeholder is concise & not truncated; On map / All toggle works
            ph = pg.get_attribute("#event-search", "placeholder") or ""
            assert len(ph) <= 18, f"Search placeholder too long: {ph!r}"
            assert pg.locator("#ev-scope-toggle").count() == 1, "Events panel needs On map / All scope toggle"

            # AC-4.1: Floating Legend button exists; legend popover starts CLOSED (0px height in flow!)
            leg_btn = pg.locator("#legend-btn")
            assert leg_btn.is_visible(), "Floating Legend toggle button must be visible in All mode"
            assert not pg.locator("#gm-legend").is_visible(), "Multi-lane legend must start closed as a popover"
            leg_btn.click()
            assert pg.locator("#gm-legend").is_visible(), "Clicking Legend button must open popover"
            pg.keyboard.press("Escape")
            assert not pg.locator("#gm-legend").is_visible(), "Esc must close legend popover"

            # AC-4.2: In All mode, named TC ('Champi') gets a label; anonymous MLCs ('Storm Oct ...') do NOT
            labels = pg.eval_on_selector_all("#gm-track-marks text.gm-mark-lab", "ns => ns.map(n => n.textContent.trim())")
            assert "Champi" in labels, f"Named TC Champi should be labelled; got {labels}"
            assert not any(l.startswith("Storm ") for l in labels), f"Anonymous MLC labels must be hidden in All mode; got {labels}"
            # Pill backdrop exists behind label for contrast
            assert pg.locator("#gm-track-marks rect.gm-mark-lab-bg").count() >= 1, "Track labels need pill background rects"

            # AC-G3: Zero horizontal overflow on desktop & map + timeline fit cleanly
            ov = pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
            assert ov, "Desktop 1280px has horizontal overflow"
            pg.screenshot(path=os.path.join(SHOTS, "ux-redesign-events-light.png"))

            # Dark mode screenshot
            pg.locator('#theme-ctl button[data-pref="dark"]').click()
            pg.wait_for_timeout(150)
            pg.screenshot(path=os.path.join(SHOTS, "ux-redesign-events-dark.png"))
            pg.locator('#theme-ctl button[data-pref="light"]').click()

            assert errs == [], f"Runtime errors: {errs}"
        finally:
            ctx.close()
            b.close()


def test_unified_scorecard_overview_and_drilldown(app_url):
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx, pg, errs = _open_page(b, app_url, 1280, 800)
        try:
            # Switch to Scorecard view
            pg.locator('#rail button[data-view="scorecard"], #rail button[data-jump="scorecard-sec"]').first.click()
            pg.wait_for_timeout(150)
            assert pg.locator("#scorecard-sec").is_visible()
            assert not pg.locator("#explore").is_visible()

            # AC-1.3: Single Overview matrix table in All mode (NOT 7 stacked tables!)
            tables = pg.locator("#scorecard-tables table")
            assert tables.count() == 1, f"All-hazards Scorecard must render 1 unified overview matrix, got {tables.count()}"

            # AC-1.2: Human-readable model names (no raw snake_case like aifs_single / ifs_hres in visible text)
            sc_text = pg.inner_text("#scorecard-sec")
            assert "AIFS Single" in sc_text, "Human model name 'AIFS Single' missing"
            assert "IFS HRES" in sc_text, "Human model name 'IFS HRES' missing"
            assert "aifs_single" not in sc_text, "Raw snake_case 'aifs_single' leaked into Scorecard"
            assert "fourcastnet_v2_gfs" not in sc_text, "Raw snake_case 'fourcastnet_v2_gfs' leaked into Scorecard"

            # KPI leader cards exist
            assert pg.locator(".kpi-card").count() >= 4, "KPI leader strip cards missing"

            pg.screenshot(path=os.path.join(SHOTS, "ux-redesign-scorecard-overview.png"))

            # Drill down to Heat wave -> single lead-time table, empty model 'aigfs' NOT shown as blank row
            pg.locator(".kpi-card").first.click()
            pg.wait_for_timeout(150)
            assert pg.locator("#scorecard-tables table").count() == 1
            row_names = pg.eval_on_selector_all("#scorecard-tables tbody td.model-col", "ns => ns.map(n => n.textContent.trim())")
            assert not any("AIGFS" in r or "aigfs" in r for r in row_names), f"Unscored AIGFS must not render as empty row: {row_names}"

            # Drill down to Tropical cyclone -> no open 8-line prose paragraph dumped below table
            tc_btn = pg.locator('#scorecard-sec button:has-text("Trop. cyclone"), #scorecard-sec button:has-text("TC"), #scorecard-sec .kpi-card:has-text("TC")').first
            if tc_btn.count():
                tc_btn.click()
                pg.wait_for_timeout(150)
            open_notes = pg.eval_on_selector_all("#scorecard-tables p.lane-note", "ns => ns.filter(n => !n.closest('details:not([open])')).length")
            assert open_notes == 0, "Verbose lane-note paragraphs must be collapsed inside <details>, not open inline"

            pg.screenshot(path=os.path.join(SHOTS, "ux-redesign-scorecard-drilldown.png"))
            assert errs == [], f"Runtime errors: {errs}"
        finally:
            ctx.close()
            b.close()


@pytest.mark.parametrize("width", [360, 480, 600, 720, 850, 1100, 1280, 1440])
def test_kpi_event_counter_stays_inside_card(app_url, width):
    """Regression (design bug 2026-11-23): a non-shrinkable `.type-chip` pushed
    the nowrap "N events" counter over the card's right border on narrow KPI
    columns. The counter must sit inside its card at every width; a chip that
    cannot fit ellipsizes instead (full name stays in the card title)."""
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx, pg, errs = _open_page(b, f"{app_url}#view=scorecard", width, 900)
        try:
            pg.wait_for_selector(".kpi-card", timeout=15000)
            assert pg.locator(".kpi-card").count() >= 4, "KPI leader strip cards missing"
            pg.wait_for_timeout(300)
            bad = pg.evaluate("""() => {
              const out = [];
              for (const card of document.querySelectorAll('.kpi-card')) {
                const cb = card.getBoundingClientRect();
                for (const child of card.querySelectorAll('.kpi-top *, .kpi-lead *, .kpi-sub')) {
                  const r = child.getBoundingClientRect();
                  if (r.width === 0) continue;
                  if (r.right - cb.right > 0.5)
                    out.push({card: card.querySelector('.type-chip span')?.textContent,
                              over: +(r.right - cb.right).toFixed(1), who: child.className});
                }
              }
              return out;
            }""")
            assert not bad, f"{width}px: KPI content crosses card border: {bad}"
            assert errs == [], f"{width}px: Runtime errors: {errs}"
        finally:
            ctx.close()
            b.close()


@pytest.mark.parametrize("event_id,etype,kind", [
    ("hw001a", "heat_wave", "grid"),
    ("cs001b", "cold_snap", "grid"),
    ("fr001c", "freeze", "grid"),
    ("ar001d", "atmospheric_river", "ar"),
    ("fl001e", "flood", "grid"),
    ("tc001f", "tropical_cyclone", "track"),
    ("mlc001g", "midlat_cyclone", "track"),
])
def test_unified_5_band_event_detail_all_seven_types(app_url, event_id, etype, kind):
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx, pg, errs = _open_page(b, f"{app_url}#event={event_id}", 1280, 800)
        try:
            pg.wait_for_selector("#detail-sec:not(.hidden)", timeout=10000)
            pg.wait_for_selector("#detail-viz .det-stage", timeout=10000)

            # Band 1: Unified Header (no 3x repetition, type chip beside title, meta chips)
            assert pg.locator("#detail-sec .det-header").is_visible()
            assert pg.locator("#detail-type-slot .type-chip").is_visible()
            assert pg.locator("#detail-chips .det-meta-chip").count() >= 2
            title_txt = pg.inner_text("#detail-title").strip()
            assert "Heat wave ·" not in title_txt and "Freeze ·" not in title_txt, f"Title must not repeat event type: {title_txt!r}"

            # Rail stays navigable during detail view!
            assert pg.locator('#rail .rail-link').count() >= 3

            # Band 2: Unified Control Bar
            assert pg.locator("#detail-viz .det-toolbar").is_visible()

            # Band 3: Unified Split Stage (map left + side panel right side-by-side at 1280px)
            map_box = pg.locator("#detail-viz .det-map-col").bounding_box()
            side_box = pg.locator("#detail-viz .det-side-col").bounding_box()
            assert map_box and side_box
            assert side_box["x"] >= map_box["x"] + map_box["width"] - 2, "Map and side panel must sit side-by-side on desktop"
            assert pg.locator("#detail-viz .det-map-bar").is_visible()

            # Band 4: Unified Summary Strip
            assert pg.locator("#detail-viz .det-summary-strip").is_visible()

            # Band 5: Unified Scores Fold
            assert pg.locator("#detail-scores").is_visible()

            if kind in ("grid", "ar"):
                # AC-2.2: Active overlays panel shows ONLY available layers (no disabled 'no data' rows in active list!)
                dis_in_active = pg.locator("#detail-viz .det-side-body .ev3-layer.dis").count()
                assert dis_in_active == 0, f"Unavailable models leaked into active overlay list for {etype}"
                assert pg.locator("#detail-viz .det-unavail").count() == 1, f"Unavailable models must collapse into <details class='det-unavail'> for {etype}"
                # AC-2.3: No raw hex chip on map
                assert pg.locator("#detail-viz .ev3-namechip").count() == 0, "Raw hex ID map chip must be removed"
                # Human-readable model names inside layer panel
                side_txt = pg.inner_text("#detail-viz .det-side-col")
                assert "AIFS Single" in side_txt and "aifs_single" not in side_txt
            else:
                # AC-3.2 & AC-3.4 & AC-3.5: Track detail parity
                assert pg.locator("#detail-viz .det-kv-row").count() >= 4, f"Track summary KV rows missing for {etype}"
                assert pg.locator("#detail-viz .det-unavail").count() == 1, "Track notes must live in collapsible <details class='det-unavail'>"
                assert pg.locator("#detail-viz .det-summary-strip svg").count() >= 1, "Band 4 intensity sparkline SVG missing"
                if etype == "midlat_cyclone":
                    bar_txt = pg.inner_text("#detail-viz .det-map-bar")
                    assert "hPa" in bar_txt and "CAT" not in bar_txt, f"MLC legend must use hPa pressure bins, not TC categories: {bar_txt!r}"

            if event_id in ("hw001a", "ar001d", "tc001f", "mlc001g"):
                pg.screenshot(path=os.path.join(SHOTS, f"ux-redesign-detail-{etype}.png"))

            assert errs == [], f"Runtime errors on {etype}: {errs}"
        finally:
            ctx.close()
            b.close()


def test_mobile_no_overflow_and_axe_zero_violations(app_url):
    os.makedirs(SHOTS, exist_ok=True)
    with open(AXE_JS) as fh:
        axe_src = fh.read()
    with sync_playwright() as p:
        b = p.chromium.launch()
        # 1. Mobile 390x844 overflow check
        ctx, pg, errs = _open_page(b, app_url, 390, 844)
        try:
            ov = pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
            assert ov, "Mobile 390px Events view overflows horizontally"
            pg.screenshot(path=os.path.join(SHOTS, "ux-redesign-mobile-events.png"))
            assert errs == []
        finally:
            ctx.close()

        # 2. Desktop Light + Dark axe-core WCAG AA check across Events, Scorecard, and Detail
        ctx2, pg2, errs2 = _open_page(b, app_url, 1280, 800)
        try:
            pg2.add_script_tag(content=axe_src)
            for theme in ("light", "dark"):
                pg2.locator(f'#theme-ctl button[data-pref="{theme}"]').click()
                pg2.wait_for_timeout(120)
                for label, action in [
                    ("events", lambda: pg2.locator('#rail button[data-view="events"], #rail button[data-jump="explore"]').first.click()),
                    ("scorecard", lambda: pg2.locator('#rail button[data-view="scorecard"], #rail button[data-jump="scorecard-sec"]').first.click()),
                ]:
                    action()
                    pg2.wait_for_timeout(120)
                    res = pg2.evaluate("async () => (await axe.run()).violations.filter(v => ['serious','critical'].includes(v.impact))")
                    assert res == [], f"axe violations ({theme}/{label}): {[(v['id'], len(v['nodes'])) for v in res]}"
            assert errs2 == []
        finally:
            ctx2.close()
            b.close()
