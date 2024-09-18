from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..runner import UserConfig


def link_or_copy(
        src: str | Path,
        dstdir: str | Path,
        filename: str | None = None,
        user: UserConfig | None = None):
    """
    Try to make a hardlink of src inside directory dstdir.

    If hardlinking is not possible, copy it
    """
    src = Path(src)
    dstdir = Path(dstdir)
    if filename is None:
        filename = src.name
    dest = dstdir / filename
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)

    if user is not None:
        os.chown(dest, user.user_id, user.group_id)
