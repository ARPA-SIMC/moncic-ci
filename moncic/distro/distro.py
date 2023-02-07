from __future__ import annotations

import logging
import os
import tempfile
from typing import TYPE_CHECKING, Iterable, NamedTuple, Optional, Type

from ..utils.osrelease import parse_osrelase

if TYPE_CHECKING:
    from ..container import ContainerConfig
    from ..system import System

log = logging.getLogger(__name__)


class DistroInfo(NamedTuple):
    """
    Information about a distribution
    """
    # Canonical name
    name: str
    shortcuts: list[str]


class DistroFamily:
    """
    Base class for handling a family of distributions
    """
    # Registry of known families
    families: dict[str, DistroFamily] = {}

    # Registry mapping known shortcut names to the corresponding full
    # ``family:version`` name
    SHORTCUTS: dict[str, str] = {}

    @classmethod
    def register(cls, family_cls: Type["DistroFamily"]) -> Type["DistroFamily"]:
        name = getattr(family_cls, "NAME", None)
        if name is None:
            name = family_cls.__name__.lower()
        cls.families[name] = family_cls()
        return family_cls

    @classmethod
    def populate(cls):
        from . import debian, rpm  # noqa

    @classmethod
    def list(cls) -> Iterable[DistroFamily]:
        cls.populate()
        return cls.families.values()

    @classmethod
    def lookup_family(cls, name: str) -> DistroFamily:
        cls.populate()
        return cls.families[name]

    @classmethod
    def lookup_distro(cls, name: str) -> Distro:
        """
        Lookup a Distro object by name.

        If the name contains a ``:``, it is taken as a full ``family:version``
        name. Otherwise, it is looked up among distribution shortcut names.
        """
        cls.populate()
        if ":" in name:
            family, version = name.split(":", 1)
            return cls.lookup_family(family).create_distro(version)
        else:
            return cls._lookup_shortcut(name)

    @classmethod
    def _lookup_shortcut(cls, name: str) -> Distro:
        """
        Lookup a Distro object by shortcut
        """
        for family in cls.families.values():
            if (fullname := family.SHORTCUTS.get(name)) is not None:
                return cls.lookup_distro(fullname)
        raise KeyError(f"Distro {name!r} not found")

    @classmethod
    def from_path(cls, path: str) -> Distro:
        """
        Instantiate a Distro from an existing filesystem tree
        """
        cls.populate()
        # For os-release format documentation, see
        # https://www.freedesktop.org/software/systemd/man/os-release.html

        # TODO: check if "{path}.yaml" exists
        info: Optional[dict[str, str]]
        try:
            info = parse_osrelase(os.path.join(path, "etc", "os-release"))
        except FileNotFoundError:
            info = None

        if info is None or "ID" not in info or "VERSION_ID" not in info:
            return cls.lookup_distro(os.path.basename(path))
        else:
            family = cls.lookup_family(info["ID"])
            return family.create_distro(info["VERSION_ID"])

    @property
    def name(self) -> str:
        """
        Name for this distribution
        """
        name = getattr(self, "NAME", None)
        if name is None:
            name = self.__class__.__name__.lower()
        return name

    def __str__(self) -> str:
        return self.name

    def create_distro(self, version: str) -> "Distro":
        """
        Create a Distro object for a distribution in this family, given its
        version
        """
        raise NotImplementedError(f"{self.__class__}.create_distro not implemented")

    def list_distros(self) -> list[DistroInfo]:
        """
        Return a list of distros available in this family
        """
        return [
            DistroInfo(name, [shortcut])
            for shortcut, name in self.SHORTCUTS.items()]


class Distro:
    """
    Common base class for bootstrapping distributions
    """
    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return self.name

    def get_base_packages(self) -> list[str]:
        """
        Return the list of packages that are expected to be installed on a
        freshly bootstrapped system
        """
        return ["bash", "dbus"]

    def container_config_hook(self, system: System, config: ContainerConfig):
        """
        Hook to allow distro-specific container setup
        """
        # Do nothing by default
        pass

    def bootstrap(self, system: System) -> None:
        """
        Boostrap a fresh system inside the given directory
        """
        # At least on Debian, mkosi does not seem able to install working
        # rpm-based distributions: https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1008169
        distro, release = self.name.split(":", 1)
        installroot = os.path.abspath(system.path)
        base_packages = ','.join(self.get_base_packages())
        with tempfile.TemporaryDirectory() as workdir:
            cmd = [
                "/usr/bin/mkosi", f"--distribution={distro}",
                f"--release={release}", "--format=directory",
                f"--output={installroot}", "--base-packages=true",
                f"--package={base_packages}", f"--directory={workdir}",
                "--force",
                # f"--mirror={self.mirror}",
            ]
            system.local_run(cmd)

        # Cleanup mkosi manifest file
        try:
            os.unlink(f"{installroot}.manifest")
        except FileNotFoundError:
            pass

    def get_setup_network_script(self, system: System) -> list[list[str]]:
        """
        Get the sequence of commands to use to setup networking
        """
        return []

    def get_update_pkgdb_script(self, system: System) -> list[list[str]]:
        """
        Get the sequence of commands to use to update package information
        """
        return []

    def get_upgrade_system_script(self, system: System) -> list[list[str]]:
        """
        Get the sequence of commands to use to update package information
        """
        return []

    def get_install_packages_script(self, system: System, packages: list[str]) -> list[list[str]]:
        """
        Get the sequence of commands to use to install packages
        """
        return []

    def get_versions(self, packages: list[str]) -> dict[str, dict[str, str]]:
        """
        Get the installed versions of packages described in the given list
        """
        raise NotImplementedError(
                f"getting installed versions for package requirements is not implemented for {self.name}")
