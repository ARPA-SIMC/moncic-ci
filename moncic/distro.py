from __future__ import annotations
import contextlib
import logging
import os
import shutil
import tempfile
from typing import Type, List, Dict, Sequence, TYPE_CHECKING

from .osrelease import parse_osrelase
from .runner import MachineRunner, SystemdRunRunner, LegacyRunRunner
if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


class DistroFamily:
    """
    Base class for handling a family of distributions
    """
    # Registry of known families
    families: Dict[str, Type[DistroFamily]] = {}

    # Registry mapping known shortcut names to the corresponding full
    # ``family:version`` name
    SHORTCUTS: Dict[str, str] = {}

    @classmethod
    def register(cls, family_cls: Type["DistroFamily"]) -> Type["DistroFamily"]:
        name = getattr(family_cls, "NAME", None)
        if name is None:
            name = family_cls.__name__.lower()
        cls.families[name] = family_cls()
        return family_cls

    @classmethod
    def list(cls) -> Sequence[DistroFamily]:
        return cls.families.values()

    @classmethod
    def lookup_family(cls, name: str) -> DistroFamily:
        return cls.families[name]

    @classmethod
    def lookup_distro(cls, name: str) -> Distro:
        """
        Lookup a Distro object by name.

        If the name contains a ``:``, it is taken as a full ``family:version``
        name. Otherwise, it is looked up among distribution shortcut names.
        """
        if ":" in name:
            family, version = name.split(":", 1)
            return cls.lookup_family(family).create_distro(version)
        else:
            for family in cls.families.values():
                if (fullname := family.SHORTCUTS.get(name)) is not None:
                    return cls.lookup_distro(fullname)
            raise KeyError(f"Distro {name!r} not found")

    @classmethod
    def from_path(cls, path: str) -> Distro:
        """
        Instantiate a Distro from an existing filesystem tree
        """
        # TODO: check if "{path}.yaml" exists
        info = parse_osrelase(os.path.join(path, "etc", "os-release"))
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


@DistroFamily.register
class Debian(DistroFamily):
    VERSION_IDS = {
        "10": "buster",
        "11": "bullseye",
        "12": "bookworm",
    }
    ALIASES = {
        "oldstable": "buster",
        "stable": "bullseye",
        "testing": "bookworm",
        "unstable": "sid",
    }
    SHORTCUTS = {
        suite: f"debian:{suite}"
        for suite in ("buster", "bullseye", "bookworm")
    }

    def create_distro(self, version: str) -> "Distro":
        # Map version numbers to release codenames
        suite = self.VERSION_IDS.get(version, version)

        if suite in ("buster", "bullseye", "bookworm", "sid", "oldstable", "stable", "testing", "unstable"):
            return DebianDistro(f"debian:{version}", suite)
        else:
            raise KeyError(f"Debian version {version!r} is not (yet) supported")


@DistroFamily.register
class Fedora(DistroFamily):
    SHORTCUTS = {
        f"fedora{version}": f"fedora:{version}"
        for version in (32, 34)
    }

    def create_distro(self, version: str) -> "Distro":
        intver = int(version)
        if intver in (32, 34):
            return FedoraDistro(f"fedora:{intver}", intver)
        else:
            raise KeyError(f"Fedora version {version!r} is not (yet) supported")


@DistroFamily.register
class Centos(DistroFamily):
    SHORTCUTS = {
        f"centos{version}": f"centos:{version}"
        for version in (7, 8)
    }

    def create_distro(self, version: str) -> "Distro":
        intver = int(version)
        if intver == 7:
            return Centos7(f"centos:{intver}")
        elif intver == 8:
            return Centos8(f"centos:{intver}")
        else:
            raise KeyError(f"Centos version {version!r} is not (yet) supported")


class Distro:
    """
    Common base class for bootstrapping distributions
    """
    runner_class: Type[MachineRunner] = SystemdRunRunner

    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return self.name

    def bootstrap(self, system: System) -> None:
        """
        Boostrap a fresh system inside the given directory
        """
        raise NotImplementedError(f"{self.__class__}.bootstrap not implemented")

    def get_update_script(self) -> List[List[str]]:
        """
        Get the sequence of commands to use for regular update/maintenance
        """
        return []


class RpmDistro(Distro):
    """
    Common implementation for rpm-based distributions
    """
    def get_base_packages(self) -> List[str]:
        """
        Return the list of packages that are expected to be installed on a
        freshly bootstrapped system
        """
        return ["bash", "rootfiles", "dbus"]

    @contextlib.contextmanager
    def chroot_config(self):
        with tempfile.NamedTemporaryFile("wt", suffix=".repo") as fd:
            print("[chroot-base]", file=fd)
            print("name=Linux $releasever - $basearch", file=fd)
            print(f"baseurl={self.baseurl}", file=fd)
            print("enabled=1", file=fd)
            print("gpgcheck=0", file=fd)
            fd.flush()
            yield fd.name

    def bootstrap(self, system: System):
        installer = shutil.which("dnf")
        if installer is None:
            installer = shutil.which("yum")
        if installer is None:
            raise RuntimeError("yum or dnf not found")

        with self.chroot_config() as dnf_config:
            installroot = os.path.abspath(system.path)
            cmd = [
                installer, "-c", dnf_config, "-y", "--disablerepo=*",
                "--enablerepo=chroot-base", "--disableplugin=*",
                f"--installroot={installroot}", f"--releasever={self.version}",
                "install"
            ] + self.get_base_packages()
            system.local_run(cmd)

            # If dnf used a private rpmdb, promote it as the rpmdb of the newly
            # created system. See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1004863#32
            private_rpmdb = os.path.join(installroot, "root", ".rpmdb")
            system_rpmdb = os.path.join(installroot, "var", "lib", "rpm")
            if os.path.isdir(private_rpmdb):
                log.info("Moving %r to %r", private_rpmdb, system_rpmdb)
                if os.path.isdir(system_rpmdb):
                    shutil.rmtree(system_rpmdb)
                shutil.move(private_rpmdb, system_rpmdb)
                with system.create_maintenance_run() as run:
                    run.run(["/usr/bin/rpmdb", "--rebuilddb"])


class YumDistro(RpmDistro):
    def get_base_packages(self) -> List[str]:
        return super().get_base_packages() + ["yum"]

    def get_update_script(self):
        res = super().get_update_script()
        return res + [
            ["/usr/bin/yum", "upgrade", "-q", "-y"]
        ]


class DnfDistro(RpmDistro):
    def get_base_packages(self) -> List[str]:
        return super().get_base_packages() + ["dnf"]

    def get_update_script(self):
        res = super().get_update_script()
        return res + [
            ["/usr/bin/dnf", "upgrade", "-q", "-y"]
        ]


class Centos7(YumDistro):
    baseurl = "http://mirror.centos.org/centos/7/os/$basearch"
    version = 7
    runner_class = LegacyRunRunner


class Centos8(DnfDistro):
    baseurl = "http://mirror.centos.org/centos-8/8/BaseOS/$basearch/os"
    version = 8


class FedoraDistro(DnfDistro):
    def __init__(self, name: str, version: int):
        super().__init__(name)
        self.version = version
        self.baseurl = f"http://download.fedoraproject.org/pub/fedora/linux/releases/{version}/Everything/$basearch/os/"


class DebianDistro(Distro):
    """
    Common implementation for Debian-based distributions
    """
    def __init__(self, name: str, suite: str):
        super().__init__(name)
        self.mirror = "http://deb.debian.org/debian"
        self.suite = suite

    def bootstrap(self, system: System):
        installroot = os.path.abspath(system.path)
        cmd = [
            "debootstrap", "--include=dbus,systemd", "--variant=minbase", self.suite, installroot, self.mirror
        ]
        # If eatmydata is available, we can use it to make deboostrap significantly faster
        eatmydata = shutil.which("eatmydata")
        if eatmydata is not None:
            cmd.insert(0, eatmydata)
        system.local_run(cmd)
