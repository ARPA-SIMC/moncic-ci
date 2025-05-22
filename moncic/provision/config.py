import logging
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Self, Any

import yaml

from moncic.distro import DistroFamily, Distro

if TYPE_CHECKING:
    from moncic.session import Session
    from moncic.image import BootstrappableImage


class BootstrapInfo(NamedTuple):
    """Information used to bootstrap an image."""

    # Name of the distribution used to bootstrap this image.
    # If missing, this image needs to be created from an existing image
    distro: str | None
    # Name of the distribution used as a base for this one.
    # If missing, this image needs to be created by bootstrapping from scratch
    extends: str | None
    # List of packages to install
    packages: list[str]
    # Contents of a script to run for system maintenance
    maintscript: str | None
    # List of users to propagate from host to image during maintenance
    forward_users: list[str]
    # When False, a CACHEDIR.TAG is created in the container image as a hint
    # for backup programs to skip backing up an image that can be recreated
    # from scratch
    backup: bool
    # Btrfs compression level to set on the OS image subvolume when it is
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: the global 'compression' setting. You can use 'no'
    # or 'none' to ask for no compression when one globally is set.
    compression: str | None

    @classmethod
    def from_distro(cls, distro: Distro) -> Self:
        return cls(
            distro=distro.name,
            extends=None,
            packages=[],
            maintscript=None,
            forward_users=[],
            backup=False,
            compression=None,
        )

    @classmethod
    def load(cls, conf: dict[str, Any]) -> Self:
        """
        Create from a parsed config file.

        Remove parsed elements from dict.
        """
        distro = conf.pop("distro", None)
        extends = conf.pop("extends", None)

        # Make sure forward_users, if present, is a list of strings

        if (fu := conf.pop("forward_user", None)) is None:
            forward_users = []
        elif isinstance(fu, str):
            forward_users = [fu]
        else:
            forward_users = [str(e) for e in fu]

        # Prepend a default shebang to the maintscript if missing
        maintscript = conf.pop("maintscript", None)
        if maintscript is not None and not maintscript.startswith("#!"):
            maintscript = "#!/bin/sh\n" + maintscript

        return cls(
            distro=distro,
            extends=extends,
            packages=conf.pop("packages", None) or [],
            maintscript=maintscript or None,
            forward_users=forward_users,
            backup=conf.pop("backup", False),
            compression=conf.pop("compression", None),
        )


class ContainerInfo(NamedTuple):
    """Information used to start a container."""

    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    #
    # Leave to None to use system or container defaults.
    tmpfs: bool | None = None

    @classmethod
    def load(cls, conf: dict[str, Any]) -> Self:
        """
        Create from a parsed config file.

        Remove parsed elements from dict.
        """
        return cls(
            tmpfs=conf.pop("tmpfs", None),
        )


class Config:
    """Configuration for an image."""

    parent: "BootstrappableImage"
    distro: Distro
    bootstrap_info: BootstrapInfo
    container_info: ContainerInfo

    def __init__(self, session: "Session", name: str, path: Path) -> None:
        self.path = path
        with self.path.open() as fd:
            self.conf = yaml.load(fd, Loader=yaml.CLoader)

        distro_name = self.conf.pop("distro", None)
        extends_name = self.conf.pop("extends", None)
        if distro_name and extends_name:
            raise RuntimeError(f"{self.path}: both 'distro' and 'extends' have been specified")
        elif not distro_name and not extends_name:
            raise RuntimeError(f"{self.path}: neither 'distro' nor 'extends' have been specified")
        parent_name = distro_name or extends_name

        # TODO: make sure we get the bootstrappableimage for it
        self.parent = session.images.parent_image(parent_name, name)
        self.distro = self.parent.distro

        self.bootstrap_info = BootstrapInfo.load(self.conf)
        self.container_info = ContainerInfo.load(self.conf)

    def warn_unsupported_entries(self, logger: logging.Logger) -> None:
        """Warn about config entries that are still unparsed."""
        for key in self.conf.keys():
            logger.debug("%s: ignoring unsupported configuration: %r", self.path, key)
