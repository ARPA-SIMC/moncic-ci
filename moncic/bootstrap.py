from __future__ import annotations
from typing import Type
import subprocess
import contextlib
import tempfile
import shutil
import os


class Distro:
    """
    Common base class for bootstrapping distributions
    """
    distros = {}

    def bootstrap_subvolume(self, path: str):
        """
        Create a btrfs subvolume at the given path and bootstrap a distribution
        tree inside it
        """
        cmd = ["btrfs", "subvolume", "create", path]
        subprocess.run(cmd, check=True)
        try:
            self.bootstrap(path)
        except Exception:
            cmd = ["btrfs", "subvolume", "delete", path]
            subprocess.run(cmd, check=True)
            raise

    @classmethod
    def register(cls, distro_cls: Type["Distro"]):
        cls.distros[distro_cls.__name__.lower()] = distro_cls
        return distro_cls

    @classmethod
    def list(cls):
        return cls.distros.keys()

    @classmethod
    def create(cls, name: str):
        distro_cls = cls.distros[name]
        return distro_cls()


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

    def bootstrap(self, destdir: str):
        with self.chroot_config() as chroot_initial:
            cmd = [
                self.installer, "-q", "-c", chroot_initial, "-y", "--disablerepo=*",
                "--enablerepo=chroot-base", "--disableplugin=*",
                f"--installroot={os.path.abspath(destdir)}", f"--releasever={self.RELEASEVER}",
                "install"
            ] + self.PACKAGES
            subprocess.run(cmd, check=True)


@Distro.register
class Centos7(Rpm):
    BASEURL = "http://mirror.centos.org/centos/7/os/$basearch"
    RELEASEVER = 7
    PACKAGES = ["bash", "vim-minimal", "yum", "rootfiles", "git", "dbus"]


@Distro.register
class Centos8(Rpm):
    BASEURL = "http://mirror.centos.org/centos-8/8/BaseOS/$basearch/os"
    RELEASEVER = 8
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "git", "dbus"]


@Distro.register
class Fedora32(Rpm):
    BASEURL = "http://download.fedoraproject.org/pub/fedora/linux/releases/32/Everything/$basearch/os/"
    RELEASEVER = 32
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "git", "dbus"]


@Distro.register
class Fedora34(Rpm):
    BASEURL = "http://download.fedoraproject.org/pub/fedora/linux/releases/34/Everything/$basearch/os/"
    RELEASEVER = 34
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "git", "dbus"]
