"""Host guard: EWB compute must run on the ewb LXC, never on this container.

The product owner's hard rule (2026-11-06): no EWB downloads/compute may run
on the Orca agent container (hostname 'orca'). All heavy entry points call
``require_ewb_host()`` at the top of their CLI ``main()`` — never at import
time, so offline unit tests keep working.

Override (for deliberate local runs only): ``EWB_LIVE_ALLOW_LOCAL=1``.
"""

from __future__ import annotations

import os
import socket
import sys

EWB_HOST = "ewb"
_MESSAGE = (
    "EWB compute must run on the ewb LXC: use ops/remote.sh\n"
    f"  (this host is '{socket.gethostname()}', required host is '{EWB_HOST}';\n"
    "  example: ops/remote.sh run python -m ewb_live.pipeline --days 90)\n"
    "  Set EWB_LIVE_ALLOW_LOCAL=1 to override deliberately."
)


def is_ewb_host() -> bool:
    """True when running on the ewb LXC or the local override is set."""
    return socket.gethostname() == EWB_HOST or os.environ.get(
        "EWB_LIVE_ALLOW_LOCAL"
    ) == "1"


def require_ewb_host() -> None:
    """Exit with a clear message unless we are on the ewb LXC."""
    if not is_ewb_host():
        print(_MESSAGE, file=sys.stderr)
        raise SystemExit(2)
