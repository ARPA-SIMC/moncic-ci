from __future__ import annotations
import logging
import os
from typing import Optional, TYPE_CHECKING

from .distro import Distro
if TYPE_CHECKING:
    from .bootstrap import Bootstrapper
    from .run import RunningSystem

log = logging.getLogger(__name__)


class System:
    """
    A system configured in the CI.

    System objects hold the system configuration and contain factory methods to
    instantiate objects used to work with, and maintain, the system
    """

    def __init__(self, path: str, name: Optional[str] = None, distro: Optional[Distro] = None):
        """
        If distro is missing, it will be autodetected from the ostree present
        at path
        """
        # Name identifying this system
        if name is None:
            self.name = os.path.basename(path)
        else:
            self.name = name
        # Root path of the ostree of this system
        self.path = os.path.abspath(path)
        # Distribution this system is based on
        if distro is None:
            self.distro = Distro.from_path(path)
        else:
            self.distro = distro

    def create_ephemeral_run(self, instance_name: Optional[str] = None) -> RunningSystem:
        """
        Boot this system in a container
        """
        # Import here to avoid an import loop
        from .run import EphemeralNspawnRunningSystem
        return EphemeralNspawnRunningSystem(self)

    def create_maintenance_run(self, instance_name: Optional[str] = None) -> RunningSystem:
        """
        Boot this system in a container
        """
        # Import here to avoid an import loop
        from .run import MaintenanceNspawnRunningSystem
        return MaintenanceNspawnRunningSystem(self)

    def create_bootstrapper(self) -> Bootstrapper:
        """
        Create a boostrapper object for this system
        """
        from .bootstrap import Bootstrapper
        return Bootstrapper(self)
