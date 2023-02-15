from __future__ import annotations

import os
import shutil
from typing import Optional

from ..runner import UserConfig


def link_or_copy(src: str, dstdir: str, filename: Optional[str] = None, user: Optional[UserConfig] = None):
    """
    Try to make a hardlink of src inside directory dstdir.

    If hardlinking is not possible, copy it
    """
    if filename is None:
        filename = os.path.basename(src)
    dest = os.path.join(dstdir, filename)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)

    if user is not None:
        os.chown(dest, user.user_id, user.group_id)
