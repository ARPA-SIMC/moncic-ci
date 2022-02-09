from __future__ import annotations
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .system import System


class Moncic:
    """
    General state of the Moncic-CI setup
    """
    def __init__(self, imagedir: str = "./images"):
        self.imagedir: str = os.path.abspath(imagedir)

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
