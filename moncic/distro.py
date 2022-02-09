from __future__ import annotations
from typing import Type, List, Dict
import contextlib
import logging
import os
import shutil
import tempfile

from .osrelease import parse_osrelase
from .runner import SystemdRunRunner, LegacyRunRunner
from .bootstrap import Bootstrapper

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

    def bootstrap(self, bootstrapper: Bootstrapper) -> None:
        """
        Boostrap a fresh system inside the given directory
        """
        raise NotImplementedError(f"{self.__class__}.bootstrap not implemented")

    def get_update_script(self) -> List[List[str]]:
        """
        Get the sequence of commands to use for regular update/maintenance
        """
        raise NotImplementedError(f"{self.__class__}.get_update_script not implemented")

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

    def bootstrap(self, bootstrapper: Bootstrapper):
        with self.chroot_config() as chroot_initial:
            cmd = [
                self.installer, "-c", chroot_initial, "-y", "--disablerepo=*",
                "--enablerepo=chroot-base", "--disableplugin=*",
                f"--installroot={os.path.abspath(bootstrapper.system.path)}", f"--releasever={self.RELEASEVER}",
                "install"
            ] + self.PACKAGES
            bootstrapper.run(cmd)


@Distro.register
class Centos7(Rpm):
    BASEURL = "http://mirror.centos.org/centos/7/os/$basearch"
    RELEASEVER = 7
    PACKAGES = ["bash", "vim-minimal", "yum", "rootfiles", "dbus"]
    runner_class = LegacyRunRunner

    def get_update_script(self):
        res = [
            ["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/yum.conf"],
        ]
        for pkg in ["epel-release", "@buildsys-build", "yum-utils", "git", "rpmdevtools"]:
            res.append(["/usr/bin/yum", "install", "-y", pkg])
        res += [
            ["/usr/bin/yum", "install", "-q", "-y", "yum-plugin-copr"],
            ["/usr/bin/yum", "copr", "enable", "-q", "-y", "simc/stable", "epel-7"],
            ["/usr/bin/yum", "upgrade", "-q", "-y"],
        ]
        return res


@Distro.register
class Centos8(Rpm):
    BASEURL = "http://mirror.centos.org/centos-8/8/BaseOS/$basearch/os"
    RELEASEVER = 8
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "dbus"]

    def get_update_script(self):
        return [
            ["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/dnf/dnf.conf"],
            ["/usr/bin/dnf", "install", "-q", "-y", "epel-release"],
            ["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(config-manager)"],
            ["/usr/bin/dnf", "config-manager", "--set-enabled", "powertools"],
            ["/usr/bin/dnf", "groupinstall", "-q", "-y", "Development Tools"],
            ["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(builddep)"],
            ["/usr/bin/dnf", "install", "-q", "-y", "git"],
            ["/usr/bin/dnf", "install", "-q", "-y", "rpmdevtools"],
            ["/usr/bin/dnf", "copr", "enable", "-y", "simc/stable"],
            ["/usr/bin/dnf", "upgrade", "-q", "-y"],
        ]


class Fedora(Rpm):
    def get_update_script(self):
        return [
            ["/usr/bin/rpmdb", "--rebuilddb"],
            ["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/dnf/dnf.conf"],
            ["/usr/bin/dnf", "install", "-y", "--allowerasing", "@buildsys-build"],
            ["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(builddep)"],
            ["/usr/bin/dnf", "install", "-q", "-y", "git"],
            ["/usr/bin/dnf", "install", "-q", "-y", "rpmdevtools"],
            ["/usr/bin/dnf", "copr", "enable", "-y", "simc/stable"],
            ["/usr/bin/dnf", "upgrade", "-q", "-y"],
        ]


@Distro.register
class Fedora32(Fedora):
    BASEURL = "http://download.fedoraproject.org/pub/fedora/linux/releases/32/Everything/$basearch/os/"
    RELEASEVER = 32
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "git", "dbus"]


@Distro.register
class Fedora34(Fedora):
    BASEURL = "http://download.fedoraproject.org/pub/fedora/linux/releases/34/Everything/$basearch/os/"
    RELEASEVER = 34
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "git", "dbus"]
