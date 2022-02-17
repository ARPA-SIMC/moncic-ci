from __future__ import annotations
from typing import Type, List, Dict
import contextlib
import logging
import os
import shutil
import tempfile
from typing import TYPE_CHECKING

from .osrelease import parse_osrelase
from .runner import SystemdRunRunner, LegacyRunRunner
if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


class Distro:
    """
    Common base class for bootstrapping distributions
    """
    # Registry of known distributions
    distros: Dict[str, Type[Distro]] = {}
    runner_class = SystemdRunRunner

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

    @classmethod
    def register(cls, distro_cls: Type["Distro"]) -> Type["Distro"]:
        name = getattr(distro_cls, "NAME", None)
        if name is None:
            name = distro_cls.__name__.lower()
        cls.distros[name] = distro_cls
        return distro_cls

    @classmethod
    def list(cls) -> List[str]:
        return list(cls.distros.keys())

    @classmethod
    def create(cls, name: str) -> "Distro":
        distro_cls = cls.distros[name]
        return distro_cls()

    @classmethod
    def from_path(cls, path: str) -> "Distro":
        """
        Instantiate a Distro from an existing filesystem tree
        """
        # TODO: check if "{path}.yaml" exists
        info = parse_osrelase(os.path.join(path, "etc", "os-release"))
        name = info["ID"] + info["VERSION_ID"]
        return cls.create(name)


class Rpm(Distro):
    """
    Common implementation for rpm-based distributions
    """
    def __init__(self):
        self.installer = shutil.which("dnf")
        if self.installer is None:
            self.installer = shutil.which("yum")
        if self.installer is None:
            raise RuntimeError("yum or dnf not found")

    @contextlib.contextmanager
    def chroot_config(self):
        with tempfile.NamedTemporaryFile("wt", suffix=".repo") as fd:
            print("[chroot-base]", file=fd)
            print("name=Linux $releasever - $basearch", file=fd)
            print(f"baseurl={self.BASEURL}", file=fd)
            print("enabled=1", file=fd)
            print("gpgcheck=0", file=fd)
            fd.flush()
            yield fd.name

    def bootstrap(self, system: System):
        with self.chroot_config() as dnf_config:
            installroot = os.path.abspath(system.path)
            cmd = [
                self.installer, "-c", dnf_config, "-y", "--disablerepo=*",
                "--enablerepo=chroot-base", "--disableplugin=*",
                f"--installroot={installroot}", f"--releasever={self.RELEASEVER}",
                "install"
            ] + self.PACKAGES
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


class Yum(Rpm):
    def get_update_script(self):
        res = super().get_update_script()
        return res + [
            ["/usr/bin/yum", "upgrade", "-q", "-y"]
        ]


class Dnf(Rpm):
    def get_update_script(self):
        res = super().get_update_script()
        return res + [
            ["/usr/bin/dnf", "upgrade", "-q", "-y"]
        ]


@Distro.register
class Centos7(Yum):
    BASEURL = "http://mirror.centos.org/centos/7/os/$basearch"
    RELEASEVER = 7
    PACKAGES = ["bash", "yum", "rootfiles", "dbus"]
    runner_class = LegacyRunRunner


@Distro.register
class Centos8(Dnf):
    BASEURL = "http://mirror.centos.org/centos-8/8/BaseOS/$basearch/os"
    RELEASEVER = 8
    PACKAGES = ["bash", "dnf", "rootfiles", "dbus"]


@Distro.register
class Fedora32(Dnf):
    BASEURL = "http://download.fedoraproject.org/pub/fedora/linux/releases/32/Everything/$basearch/os/"
    RELEASEVER = 32
    PACKAGES = ["bash", "dnf", "rootfiles", "dbus"]


@Distro.register
class Fedora34(Dnf):
    BASEURL = "http://download.fedoraproject.org/pub/fedora/linux/releases/34/Everything/$basearch/os/"
    RELEASEVER = 34
    PACKAGES = ["bash", "dnf", "rootfiles", "dbus"]
