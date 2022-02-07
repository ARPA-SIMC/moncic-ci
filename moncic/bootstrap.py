from __future__ import annotations
import contextlib
import logging
import os
from typing import List, Dict, Any, TYPE_CHECKING

from .runner import LocalRunner
from .btrfs import Subvolume
if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


class Bootstrapper(contextlib.ExitStack):
    """
    Infrastructure used to bootstrap a System
    """
    def __init__(self, system: System):
        super().__init__()
        self.system = system

    def run(self, cmd: List[str], **kw) -> Dict[str, Any]:
        """
        Wrapper around subprocess.run which logs what is run
        """
        if os.path.exists(self.system.root):
            kw.setdefault("cwd", self.system.root)
        runner = LocalRunner(cmd, **kw)
        return runner.run()

    def bootstrap(self):
        subvolume = Subvolume(self)
        with subvolume.create():
            if os.path.exists(self.system.root + ".tar.gz"):
                # Shortcut in case we have a chroot in a tarball
                self.run(["tar", "-C", self.system.root, "-zxf", self.system.root + ".tar.gz"])
            else:
                self.system.distro.bootstrap(self)
