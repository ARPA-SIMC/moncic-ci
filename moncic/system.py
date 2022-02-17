from __future__ import annotations
import dataclasses
import logging
import os
from typing import List, Optional, TYPE_CHECKING

import yaml

from .distro import Distro

if TYPE_CHECKING:
    import subprocess

    from .run import RunningSystem
    from .moncic import Moncic

log = logging.getLogger(__name__)


@dataclasses.dataclass
class Config:
    """
    Configuration for a system
    """
    # Image name
    name: str
    # Path to the image on disk
    path: str
    # Name of the distribution used to bootstrap this image.
    # If missing, this image needs to be created from an existing image
    distro: Optional[str] = None
    # Name of the distribution used as a base for this one.
    # If missing, this image needs to be created by bootstrapping from scratch
    parent: Optional[str] = None
    # Contents of a script to run for system maintenance
    maintscript: Optional[str] = None

    @classmethod
    def load(cls, path):
        """
        Load the configuration from the given path.

        If a .yaml file exists, it is used.

        Otherwise, if an os tree exists, configuration is inferred from it.

        Otherwise, configuration is inferred from the basename of the path,
        which is assumed to be a distribution name.
        """
        name = os.path.basename(path)
        try:
            with open(f"{path}.yaml", "rt") as fd:
                conf = yaml.load(fd, Loader=yaml.CLoader)
        except FileNotFoundError:
            conf = {}
            if os.path.exists(path):
                conf["distro"] = Distro.from_path(path).name
            else:
                conf["distro"] = name

        conf["name"] = name
        conf["path"] = os.path.abspath(path)

        # Prepend a default shebang to the maintscript if missing
        maintscript = conf.get("maintscript")
        if maintscript is not None and not maintscript.startswith("#!"):
            conf["maintscript"] = "#!/bin/sh\n" + maintscript

        has_distro = "distro" in conf
        has_parent = "parent" in conf
        if has_distro and has_parent:
            raise RuntimeError(f"{name}: both 'distro' and 'parent' have been specified")
        elif not has_distro and not has_parent:
            raise RuntimeError(f"{name}: neither 'distro' nor 'parent' have been specified")

        allowed_names = {f.name for f in dataclasses.fields(Config)}
        if unsupported_names := conf.keys() - allowed_names:
            for name in unsupported_names:
                log.debug("%s: ignoring unsupported configuration: %r", path, name)
                del conf[name]

        return cls(**conf)


class System:
    """
    A system configured in the CI.

    System objects hold the system configuration and contain factory methods to
    instantiate objects used to work with, and maintain, the system
    """

    def __init__(self, moncic: Moncic, config: Config):
        self.moncic = moncic
        self.config = config

    @classmethod
    def from_path(cls, moncic: Moncic, path: str):
        """
        Create a System from the ostree or configuration at the given path
        """
        return cls(moncic, Config.load(path))

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{self.distro}@{self.path}"

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def path(self) -> str:
        return self.config.path

    @property
    def distro(self) -> Distro:
        """
        Return the distribution this system is based on
        """
        if self.config.distro is None:
            return Distro.from_path(self.config.path)
        else:
            return Distro.create(self.config.distro)

    def get_distro_tarball(self) -> Optional[str]:
        """
        Return the path to a tarball that can be used to bootstrap a chroot for
        this system.

        Return None if no such tarball is present
        """
        distro_name = self.config.distro
        if distro_name is None:
            raise RuntimeError("get_distro_tarball called on a system that is bootstrapped by snapshotting")
        tarball_path = os.path.join(self.moncic.imagedir, distro_name + ".tar.gz")
        if os.path.exists(tarball_path):
            return tarball_path
        else:
            return None

    def local_run(self, cmd: List[str], **kw) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.

        This is used for bootstrapping or removing a system.
        """
        # Import here to avoid dependency loops
        from .runner import LocalRunner
        if os.path.exists(self.path):
            kw.setdefault("cwd", self.path)
        runner = LocalRunner(cmd, **kw)
        return runner.execute()

        raise NotImplementedError(f"{self.__class__}.local_run() not implemented")

    def bootstrap(self):
        """
        Create a system that is missing from disk
        """
        # Import here to avoid an import loop
        from .btrfs import Subvolume
        tarball_path = self.get_distro_tarball()
        subvolume = Subvolume(self)
        with subvolume.create():
            if tarball_path is not None:
                # Shortcut in case we have a chroot in a tarball
                self.local_run(["tar", "-C", self.path, "-zxf", tarball_path])
            else:
                self.distro.bootstrap(self)

    def update(self):
        """
        Run periodic maintenance on the system
        """
        with self.create_maintenance_run() as run:
            for cmd in self.distro.get_update_script():
                run.run(cmd)
            if self.config.maintscript is not None:
                run.run_script(self.config.maintscript)

    def remove(self):
        """
        Completely remove a system image from disk
        """
        # Import here to avoid an import loop
        from .btrfs import Subvolume
        subvolume = Subvolume(self)
        subvolume.remove()

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
