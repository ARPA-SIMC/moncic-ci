from __future__ import annotations
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .distro import Distro
    from .bootstrap import Bootstrapper
    from .run import RunningSystem

log = logging.getLogger(__name__)


class System:
    """
    A system configured in the CI.

    System objects hold the system configuration and contain factory methods to
    instantiate objects used to work with, and maintain, the system
    """

    def __init__(self, name: str, root: str, distro: Distro):
        # Name identifying this system
        self.name = name
        # Root path of the ostree of this system
        self.root = root
        # Distribution this system is based on
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

#     def run_shell(
#             self,
#             ostree: str,
#             ephemeral: bool = True,
#             checkout: Optional[str] = None,
#             workdir: Optional[str] = None,
#             bind: List[str] = None,
#             bind_ro: List[str] = None):
#         """
#         Open a shell on the given ostree
#         """
#         def escape_bind_ro(s: str):
#             r"""
#             Escape a path for use in systemd-nspawn --bind-ro.
#
#             Man systemd-nspawn says:
#
#               Backslash escapes are interpreted, so "\:" may be used to embed
#               colons in either path.
#             """
#             return s.replace(":", r"\:")
#
#         with self.checkout(checkout) as repo_path:
#             cmd = ["systemd-nspawn", "-D", ostree]
#             if ephemeral:
#                 cmd.append("--ephemeral")
#
#             if bind:
#                 for pathspec in bind:
#                     cmd.append("--bind=" + pathspec)
#             if bind_ro:
#                 for pathspec in bind_ro:
#                     cmd.append("--bind-ro=" + pathspec)
#
#             if repo_path is not None:
#                 name = os.path.basename(repo_path)
#                 if name.startswith("."):
#                     raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")
#
#                 cmd.append(f"--bind={escape_bind_ro(repo_path)}:/root/{escape_bind_ro(name)}")
#                 cmd.append(f"--chdir=/root/{name}")
#             elif workdir is not None:
#                 workdir = os.path.abspath(workdir)
#                 name = os.path.basename(workdir)
#                 if name.startswith("."):
#                     raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")
#                 cmd.append(f"--bind={escape_bind_ro(workdir)}:/root/{escape_bind_ro(name)}")
#                 cmd.append(f"--chdir=/root/{name}")
#
#             self.run(cmd)
