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
    from .moncic import MoncicConfig

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
    def load(cls, mconfig: MoncicConfig, imagedir: str, name: str):
        """
        Load the configuration from the given path setup.

        If a .yaml file exists, it is used.

        Otherwise, if an os tree exists, configuration is inferred from it.

        Otherwise, configuration is inferred from the basename of the path,
        which is assumed to be a distribution name.
        """
        for path in [imagedir] + mconfig.imageconfdirs:
            conf_pathname = os.path.join(path, name)
            log.debug("%s: look for configuration on %s", name, conf_pathname)
            try:
                with open(f"{conf_pathname}.yaml", "rt") as fd:
                    conf = yaml.load(fd, Loader=yaml.CLoader)
                log.debug("%s: configuration found at %s", name, conf_pathname)
                break
            except FileNotFoundError:
                pass
        else:
            conf = None
            conf_pathname = None

        image_pathname = os.path.abspath(os.path.join(imagedir, name))
        log.debug("%s: image pathname: %s", name, image_pathname)

        if conf is None:
            conf = {}
            if os.path.exists(image_pathname):
                conf["distro"] = DistroFamily.from_path(image_pathname).name
            else:
                conf["distro"] = name

        conf["name"] = name
        conf["path"] = image_pathname

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

    @property
    def logger(self):
        """
        Return a logger for this system
        """
        return logging.getLogger(f"system.{self.name}")


class System:
    """
    A system configured in the CI.

    System objects hold the system configuration and contain factory methods to
    instantiate objects used to work with, and maintain, the system
    """

    def __init__(self, images: Images, config: SystemConfig, path: Optional[str] = None):
        self.images = images
        self.config = config
        self.log = config.logger
        if path is None:
            self.path = self.config.path
        else:
            self.path = path

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{self.distro}@{self.path}"

    @property
    def name(self) -> str:
        return self.config.name

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

    def local_run(self, cmd: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.

        This is used for bootstrapping or removing a system.
        """
        # Import here to avoid dependency loops
        from .runner import LocalRunner
        return LocalRunner.run(self.log, cmd, config, self.config)

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
            container.forward_user(UserConfig.from_user(u), allow_maint=True)

        if self.config.maintscript is not None:
            # Run maintscripts configured for this system
            container.run_script(self.config.maintscript)
        else:
            # Or run the default standard distro maintenance
            for cmd in self.distro.get_update_script():
                container.run(cmd)

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

        # Force ephemeral to True in plain systems
        config.ephemeral = True

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
    def container_config(self, config: Optional[ContainerConfig] = None) -> ContainerConfig:
        config = super().container_config(config)
        # Force ephemeral to False in maintenance systems
        config.ephemeral = False
        return config

    def update(self):
        """
        Run periodic maintenance on the system
        """
        with self.create_container(config=ContainerConfig(ephemeral=False)) as container:
            self._update_container(container)
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
