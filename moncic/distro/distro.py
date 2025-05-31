import abc
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, override

from moncic.utils.osrelease import parse_osrelase
from moncic.utils.script import Script

if TYPE_CHECKING:
    from moncic.container import ContainerConfig
    from moncic.image import Image
    from moncic.images import Images

log = logging.getLogger(__name__)


class DistroFamily(abc.ABC):
    """
    Base class for handling a family of distributions
    """

    # Registry of known families
    families: ClassVar[dict[str, "DistroFamily"]] = {}
    # Index distros by lookup names
    distro_lookup: ClassVar[dict[str, "Distro"]] = {}

    # Registry mapping known shortcut names to the corresponding full
    # ``family:version`` name
    SHORTCUTS: dict[str, str] = {}

    @override
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register subclasses."""
        super().__init_subclass__(**kwargs)
        name: str | None
        if (name := getattr(cls, "NAME", None)) is None:
            name = cls.__name__.lower()
        cls.families[name] = cls(name)

    def __init__(self, name: str) -> None:
        self.name = name
        self.distros: list["Distro"] = []
        self.init()

    @abc.abstractmethod
    def init(self) -> None:
        """Populate self.distros via add_distro."""

    @classmethod
    def add_distro_lookup(cls, distro: "Distro") -> None:
        cls.distro_lookup[distro.full_name] = distro
        for alias in distro.aliases:
            cls.distro_lookup[alias] = distro

    def add_distro(self, distro: "Distro") -> None:
        """Add a distro to this family."""
        self.distros.append(distro)
        self.add_distro_lookup(distro)

    @override
    def __str__(self) -> str:
        return self.name

    @classmethod
    def list_families(cls) -> Iterable["DistroFamily"]:
        return cls.families.values()

    @classmethod
    def lookup_family(cls, name: str) -> "DistroFamily":
        return cls.families[name]

    @classmethod
    def lookup_distro(cls, name: str) -> "Distro":
        """
        Lookup a Distro object by name.

        If the name contains a ``:``, it is taken as a full ``family:version``
        name. Otherwise, it is looked up among distribution shortcut names.
        """
        if res := cls.distro_lookup.get(name):
            return res
        raise KeyError(f"Distro {name!r} not found")

    @classmethod
    def from_path(cls, path: Path) -> "Distro":
        """
        Instantiate a Distro from an existing filesystem tree
        """
        # For os-release format documentation, see
        # https://www.freedesktop.org/software/systemd/man/os-release.html

        # TODO: check if "{path}.yaml" exists
        info: dict[str, str] | None
        try:
            info = parse_osrelase(path / "etc" / "os-release")
        except FileNotFoundError:
            info = None

        if info is None:
            return cls.lookup_distro(path.name)

        return cls.from_osrelease(info, path.name)

    @classmethod
    def from_osrelease(cls, info: dict[str, str], fallback_name: str) -> "Distro":
        """
        Instantiate a Distro from a parsed os-release file
        """
        if (os_id := info.get("ID")) is None:
            return cls.lookup_distro(fallback_name)

        os_version = info.get("VERSION_ID")
        if os_version is None and os_id == "debian":
            os_version = "sid"

        if os_version is None:
            return cls.lookup_distro(fallback_name)

        names: list[str] = [f"{os_id}:{os_version}"]
        if "." in os_version:
            names.append(f"{os_id}:{os_version.split(".")[0]}")

        for name in names:
            if res := cls.distro_lookup.get(name):
                return res

        raise KeyError(
            f"Distro ID={os_id!r}, VERSION_ID={os_version!r} not found."
            f" Tried: {', '.join(repr(name) for name in names)} "
        )


class Distro(abc.ABC):
    """
    Common base class for bootstrapping distributions
    """

    SHORTCUTS: dict[str, str]

    def __init__(
        self,
        family: DistroFamily,
        name: str,
        version: str | None,
        other_names: list[str] | None = None,
        cgroup_v1: bool = False,
    ) -> None:
        self.family = family
        self.name = name
        self.version = version
        self.other_names = other_names or []
        self.cgroup_v1 = cgroup_v1

    @override
    def __str__(self) -> str:
        return self.name

    @property
    def full_name(self) -> str:
        return self.family.name + ":" + self.name

    @property
    def aliases(self) -> list[str]:
        res: list[str] = []
        if not self.name[0].isdigit():
            res.append(self.name)
        if self.version is not None:
            if self.name != self.version:
                res.append(f"{self.family.name}:{self.version}")
            res.append(f"{self.family.name}{self.version}")
        for other_name in self.other_names:
            res.append(f"{other_name}")
            res.append(f"{self.family.name}:{other_name}")
        return res

    def get_base_packages(self) -> list[str]:
        """
        Return the list of packages that are expected to be installed on a
        freshly bootstrapped system
        """
        return ["bash", "dbus"]

    def container_config_hook(self, image: "Image", config: "ContainerConfig") -> None:
        """
        Hook to allow distro-specific container setup
        """
        # Do nothing by default

    def _bootstrap_mkosi(self, images: "Images", path: Path) -> None:
        # At least on Debian, mkosi does not seem able to install working
        # rpm-based distributions: https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1008169
        installroot = path.absolute()
        base_packages = ",".join(self.get_base_packages())
        with tempfile.TemporaryDirectory() as workdir:
            cmd = [
                "/usr/bin/mkosi",
                f"--distribution={self.family.name}",
                f"--release={self.name}",
                "--format=directory",
                f"--output-directory={installroot.parent}",
                f"--output={installroot.name}",
                f"--package={base_packages}",
                f"--directory={workdir}",
                "--force",
                # f"--mirror={self.mirror}",
            ]
            images.host_run(cmd)

        # Cleanup mkosi manifest file
        try:
            os.unlink(f"{installroot}.manifest")
        except FileNotFoundError:
            pass

    def bootstrap(self, images: "Images", path: Path) -> None:
        """
        Boostrap a fresh system inside the given directory
        """
        self._bootstrap_mkosi(images, path)

    def get_setup_network_script(self, script: Script) -> None:
        """Add commands to use to setup networking."""

    def get_update_pkgdb_script(self, script: Script) -> None:
        """Add commands to use to update package information."""

    def get_upgrade_system_script(self, script: Script) -> None:
        """Add commands to use to upgrade system packages."""

    def get_install_packages_script(self, script: Script, packages: list[str]) -> None:
        """Add commands to use to install packages."""

    def get_prepare_build_script(self, script: Script) -> None:
        """Add commands to use to prepare a build system."""

    def get_versions(self, packages: list[str]) -> dict[str, dict[str, str]]:
        """
        Get the installed versions of packages described in the given list
        """
        raise NotImplementedError(
            f"getting installed versions for package requirements is not implemented for {self.name}"
        )

    @abc.abstractmethod
    def get_podman_name(self) -> tuple[str, str]:
        """Get the podman repository and tag for loading this distro from known repositories."""
