import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Self

import yaml

from moncic.runner import LocalRunner
from moncic.image import Image, ImageType
from moncic.distro import DistroFamily

if TYPE_CHECKING:
    from moncic.moncic import MoncicConfig
    from .images import NspawnImages

log = logging.getLogger("nspawn")


class NspawnImage(Image):
    """
    Configuration for a system
    """

    images: "NspawnImages"

    def __init__(self, *, images: "NspawnImages", name: str, path: Path) -> None:
        super().__init__(images=images, image_type=ImageType.NSPAWN, name=name)
        # Path to the image on disk
        self.path: Path = path
        # Name of the distribution used to bootstrap this image.
        # If missing, this image needs to be created from an existing image
        self.distro: str | None = None
        # Name of the distribution used as a base for this one.
        # If missing, this image needs to be created by bootstrapping from scratch
        self.extends: str | None = None
        # List of packages to install
        self.packages: list[str] = []
        # Contents of a script to run for system maintenance
        self.maintscript: str | None = None
        # List of users to propagate from host to image during maintenance
        self.forward_users: list[str] = []
        # When False, a CACHEDIR.TAG is created in the container image as a hint
        # for backup programs to skip backing up an image that can be recreated
        # from scratch
        self.backup: bool = False
        # Btrfs compression level to set on the OS image subvolume when it is
        # created. The value is the same as can be set by `btrfs property set
        # compression`. Default: the global 'compression' setting. You can use 'no'
        # or 'none' to ask for no compression when one globally is set.
        self.compression: str | None = None
        # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
        #
        # Leave to None to use system or container defaults.
        self.tmpfs: bool | None = None

    @classmethod
    def find_config(cls, mconfig: "MoncicConfig", imagedir: Path, name: str) -> Path | None:
        """
        Find the configuration file for the given image
        """
        for path in [imagedir] + mconfig.imageconfdirs:
            conf_path = path / f"{name}.yaml"
            log.debug("%s: look for configuration on %s", name, conf_path)
            try:
                if conf_path.exists():
                    log.debug("%s: configuration found at %s", name, conf_path)
                    return conf_path
            except PermissionError:
                pass
        return None

    @classmethod
    def load(cls, mconfig: "MoncicConfig", images: "NspawnImages", name: str) -> Self:
        """
        Load the configuration from the given path setup.

        If a .yaml file exists, it is used.

        Otherwise, if an os tree exists, configuration is inferred from it.

        Otherwise, configuration is inferred from the basename of the path,
        which is assumed to be a distribution name.
        """
        if conf_path := cls.find_config(mconfig, images.imagedir, name):
            with conf_path.open() as fd:
                conf = yaml.load(fd, Loader=yaml.CLoader)
        else:
            conf = None

        image_path = (images.imagedir / name).absolute()
        log.debug("%s: image pathname: %s", name, image_path)
        image = cls(images=images, name=name, path=image_path)

        if conf is None:
            try:
                if image_path.exists():
                    image.distro = DistroFamily.from_path(image_path).name
                else:
                    image.distro = name
            except PermissionError:
                image.distro = name
        else:
            has_distro = "distro" in conf
            has_extends = "extends" in conf
            if has_distro and has_extends:
                raise RuntimeError(f"{name}: both 'distro' and 'extends' have been specified")
            elif not has_distro and not has_extends:
                raise RuntimeError(f"{name}: neither 'distro' nor 'extends' have been specified")
            image.distro = conf.pop("distro", None)
            image.extends = conf.pop("extends", None)

            # Make sure forward_users, if present, is a list of strings
            forward_users = conf.pop("forward_user", None)
            if forward_users is None:
                pass
            elif isinstance(forward_users, str):
                image.forward_users = [forward_users]
            else:
                image.forward_users = [str(e) for e in forward_users]

            # Prepend a default shebang to the maintscript if missing
            maintscript = conf.pop("maintscript", None)
            if maintscript is not None and not maintscript.startswith("#!"):
                image.maintscript = "#!/bin/sh\n" + maintscript
            else:
                image.maintscript = maintscript

            if packages := conf.pop("packages", None):
                image.packages = packages

            image.backup = conf.pop("backup", False)
            image.compression = conf.pop("compression", None)
            image.tmpfs = conf.pop("tmpfs", None)

            for key in conf.keys():
                log.debug("%s: ignoring unsupported configuration: %r", conf_path, key)

        return image

    def local_run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.
        """
        return LocalRunner.run(self.logger, cmd, system_config=self)


class NspawnImagePlain(NspawnImage):
    pass


class NspawnImageBtrfs(NspawnImage):
    pass
