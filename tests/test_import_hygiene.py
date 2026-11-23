"""Import hygiene (CI hang of 2026-11-23): importing ewb_live scoring code must
not pull `extremeweatherbench.defaults`, whose module scope opens the hosted
CIRA icechunk store (network). A stalled egress path turned that into a
30-minute silent hang during pytest collection on the runner."""
import subprocess
import sys

GUARD = (
    "import sys, extremeweatherbench; "
    "import ewb_live.scoring, ewb_live.scoring.evaluate, ewb_live.pipeline; "
    "assert 'extremeweatherbench.defaults' not in sys.modules, "
    "'ewb_live import pulls extremeweatherbench.defaults (network at import!)'"
)


def test_ewb_live_import_never_pulls_ewb_defaults():
    r = subprocess.run([sys.executable, "-c", GUARD], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
