from __future__ import annotations
import os
from typing import List, Optional, TYPE_CHECKING

from .privs import ProcessPrivs

if TYPE_CHECKING:
    from .system import System


class Moncic:
    """
    General state of the Moncic-CI setup
    """
    def __init__(self, imagedir: str = "./images", privs: Optional[ProcessPrivs] = None):
        self.imagedir: str = os.path.abspath(imagedir)
        self.privs: ProcessPrivs
        if privs is None:
            self.privs = ProcessPrivs()
        else:
            self.privs = privs

    def create_system(self, name_or_path: str) -> System:
        """
        Instantiate a System from its name or path
        """
        # Import here to prevent import loops
        from .system import System
        if os.path.isdir(name_or_path):
            return System.from_path(self, name_or_path)
        else:
            return System.from_path(self, os.path.join(self.imagedir, name_or_path))

    def list_images(self) -> List[str]:
        """
        List the names of images found in image directories
        """
        res = set()
        for entry in os.scandir(self.imagedir):
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                res.add(entry.name)
            elif entry.name.endswith(".yaml"):
                res.add(entry.name[:-5])
        return sorted(res)
