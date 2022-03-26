from __future__ import annotations
import dataclasses
import logging
import os
from typing import List, Optional, TYPE_CHECKING

import yaml

from .distro import DistroFamily
from .container import ContainerConfig, RunConfig, UserConfig

if TYPE_CHECKING:
    import subprocess

    from .container import Container
    from .distro import Distro
    from .imagestorage import Images

log = logging.getLogger(__name__)


@dataclasses.dataclass
class SystemConfig:
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
    extends: Optional[str] = None
    # Contents of a script to run for system maintenance
    maintscript: Optional[str] = None
    # List of users to propagate from host to image during maintenance
    forward_users: List[str] = dataclasses.field(default_factory=list)
    # When False, a CACHEDIR.TAG is created in the container image as a hint
    # for backup programs to skip backing up an image that can be recreated
    # from scratch
    backup: bool = False
    # Btrfs compression level to set on the OS image subvolume when it is
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: the global 'compression' setting. You can use 'no'
    # or 'none' to ask for no compression when one globally is set.
    compression: Optional[str] = None
    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    #
    # Leave to None to use system or container defaults.
    tmpfs: Optional[bool] = None

    @classmethod
    def load(cls, path: str):
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
            conf = None

        if conf is None:
            conf = {}
            if os.path.exists(path):
                conf["distro"] = DistroFamily.from_path(path).name
            else:
                conf["distro"] = name

        conf["name"] = name
        conf["path"] = os.path.abspath(path)

        # Make sure forward_users, if present, is a list of strings
        forward_users = conf.pop("forward_user", None)
        if forward_users is None:
            pass
        elif isinstance(forward_users, str):
            conf["forward_users"] = [forward_users]
        else:
            conf["forward_users"] = [str(e) for e in forward_users]

        # Prepend a default shebang to the maintscript if missing
        maintscript = conf.get("maintscript")
        if maintscript is not None and not maintscript.startswith("#!"):
            conf["maintscript"] = "#!/bin/sh\n" + maintscript

        has_distro = "distro" in conf
        has_extends = "extends" in conf
        if has_distro and has_extends:
            raise RuntimeError(f"{name}: both 'distro' and 'extends' have been specified")
        elif not has_distro and not has_extends:
            raise RuntimeError(f"{name}: neither 'distro' nor 'extends' have been specified")

        allowed_names = {f.name for f in dataclasses.fields(SystemConfig)}
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

    def __init__(self, images: Images, config: SystemConfig):
        self.images = images
        self.config = config
        self.log = logging.getLogger(f"system.{self.name}")

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
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                return parent.distro
        elif self.config.distro is not None:
            return DistroFamily.lookup_distro(self.config.distro)
        else:
            raise RuntimeError("System configuration has neither `extends` nor `distro` set")

    def is_bootstrapped(self):
        """
        Check if the image has been bootstrapped
        """
        return os.path.exists(self.path)

    def get_distro_tarball(self) -> Optional[str]:
        """
        Return the path to a tarball that can be used to bootstrap a chroot for
        this system.

        Return None if no such tarball is present
        """
        distro_name = self.config.distro
        if distro_name is None:
            raise RuntimeError("get_distro_tarball called on a system that is bootstrapped by snapshotting")
        for ext in ('.tar.gz', '.tar.xz', '.tar'):
            tarball_path = os.path.join(self.images.imagedir, distro_name + ext)
            if os.path.exists(tarball_path):
                return tarball_path
        return None

    def local_run(self, cmd: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.

        This is used for bootstrapping or removing a system.
        """
        # Import here to avoid dependency loops
        from .runner import LocalRunner
        if config is None:
            config = RunConfig()
        if os.path.exists(self.path) and config.cwd is None:
            config.cwd = self.path

        runner = LocalRunner(self, config, cmd)
        return runner.execute()

    def bootstrap(self):
        """
        Create a system that is missing from disk
        """
        # Import here to avoid an import loop
        from .btrfs import Subvolume
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                subvolume = Subvolume(self)
                subvolume.snapshot(parent.path)
        else:
            tarball_path = self.get_distro_tarball()
            subvolume = Subvolume(self)
            with subvolume.create():
                if tarball_path is not None:
                    # Shortcut in case we have a chroot in a tarball
                    self.local_run(["tar", "-C", self.path, "-axf", tarball_path])
                else:
                    self.distro.bootstrap(self)

    def update(self):
        """
        Run periodic maintenance on the system
        """
        with self.create_container(config=ContainerConfig(ephemeral=False)) as container:
            self._update_container(container)

    def _update_container(self, container: Container):
        """
        Run update machinery on a container
        """
        # Base maintenance
        if self.config.extends is not None:
            # Chain to the parent's maintenance
            with self.images.system(self.config.extends) as parent:
                parent._update_container(container)

        # Forward users if needed
        for u in self.config.forward_users:
            container.forward_user(UserConfig.from_user(u))

        if self.config.maintscript is not None:
            # Run maintscripts configured for this system
            container.run_script(self.config.maintscript)
        else:
            # Or run the default standard distro maintenance
            for cmd in self.distro.get_update_script():
                container.run(cmd)

        self._update_cachedir()

    def _update_cachedir(self):
        """
        Create or remove a CACHEDIR.TAG file, depending on the image
        configuration
        """
        cachedir_pathname = os.path.join(self.path, "CACHEDIR.TAG")
        if self.config.backup:
            try:
                os.unlink(cachedir_pathname)
            except FileNotFoundError:
                pass
        else:
            if not os.path.exists(cachedir_pathname):
                with open(cachedir_pathname, "wt") as fd:
                    # See https://bford.info/cachedir/
                    print("Signature: 8a477f597d28d172789f06886806bc55", file=fd)
                    print("# This file hints to backup software that they can skip this directory.", file=fd)
                    print("# See https://bford.info/cachedir/", file=fd)

    def remove(self):
        """
        Completely remove a system image from disk
        """
        # Import here to avoid an import loop
        from .btrfs import Subvolume
        subvolume = Subvolume(self)
        subvolume.remove()

    def container_config(self, config: Optional[ContainerConfig] = None) -> ContainerConfig:
        """
        Create or complete a ContainerConfig
        """
        if config is None:
            config = ContainerConfig()
            if self.config.tmpfs is not None:
                config.tmpfs = self.config.tmpfs
            else:
                config.tmpfs = self.images.moncic.config.tmpfs
        elif config.ephemeral and config.tmpfs is None:
            # Make a copy to prevent changing the caller's config
            config = dataclasses.replace(config)
            if self.config.tmpfs is not None:
                config.tmpfs = self.config.tmpfs
            else:
                config.tmpfs = self.images.moncic.config.tmpfs

        return config

    def create_container(
            self, instance_name: Optional[str] = None, config: Optional[ContainerConfig] = None) -> Container:
        """
        Boot a container with this system
        """
        config = self.container_config(config)

        # Import here to avoid an import loop
        from .container import NspawnContainer
        return NspawnContainer(self, config, instance_name)


class MaintenanceSystem(System):
    """
    System used to do maintenance on an OS image
    """
