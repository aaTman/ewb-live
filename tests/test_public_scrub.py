"""Public leak gate: served files must not carry private-infrastructure
strings (owner rule 2026-11-08). Mirrors scripts/public_scrub_check.sh."""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "public_scrub_check.sh"


def test_repo_public_surface_clean():
    r = subprocess.run(["bash", str(SCRIPT), str(REPO)],
                       capture_output=True, text=True)
    assert r.returncode == 0, "public leak gate failed:\n" + r.stdout + r.stderr


def test_gate_detects_planted_leaks(tmp_path):
    for rel in ["dashboard/x.html", "data/results/evil.json"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("see 192.168.0.121 and /opt/ewb-live and mandelhub and CT214 "
                     "and python -m ewb_live.serve --port 8050\n")
    r = subprocess.run(["bash", str(SCRIPT), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "LEAK" in r.stdout


def test_public_meta_drops_perf_and_host():
    """G-31: meta.json is public; perf/host telemetry must be stripped."""
    from ewb_live.pipeline import public_meta
    meta = {"full_run_complete": True, "perf": {"peak_total_rss_gb": 210.4},
            "hostname": "box", "host": {"cores": 16}, "n_rows": 28608}
    pub = public_meta(meta)
    assert pub == {"full_run_complete": True, "n_rows": 28608}


def test_public_reason_single_line_no_paths():
    """G-30: pair_status.reason is public; sanitize raw exception text."""
    from ewb_live.pipeline import public_reason
    r = public_reason("failed: KeyError: 'x'\n at /opt/ewb-live/ewb_live/x.py line 3\n" + "y" * 300)
    assert "\n" not in r and len(r) <= 120
    assert "/opt/" not in r
    assert "yyy" in r
