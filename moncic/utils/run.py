from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Sequence

log = logging.getLogger(__name__)


def fix_logging_on_guest():
    """
    When running a fuction in the guest system, logging is reinitialized, but
    the `log` value remains a reference to the old logger. Recreate it here.
    """
    global log
    log = logging.getLogger(__name__)


def run(cmd: Sequence[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    """
    Logging wrapper to subprocess.run.

    Also, default check to True.
    """
    log.info("Run: %s", " ".join(shlex.quote(x) for x in cmd))
    return subprocess.run(cmd, check=check, **kw)
