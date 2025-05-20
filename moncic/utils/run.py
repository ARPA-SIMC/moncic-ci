from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger("run")


def log_run(cmd: Sequence[str], **kw) -> None:
    """
    Log executing a command
    """
    if cwd := kw.get("cwd"):
        prompt = cwd.as_posix() if isinstance(cwd, Path) else cwd
    else:
        prompt = os.getcwd()
    if os.getuid() == 0:
        prompt += "#"
    else:
        prompt += "$"

    log.info("%s %s", prompt, shlex.join(cmd))


def run(cmd: Sequence[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    """
    Logging wrapper to subprocess.run.

    Also, default check to True.
    """
    log_run(cmd, **kw)
    return subprocess.run(cmd, check=check, **kw)
