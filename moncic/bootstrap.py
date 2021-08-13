from __future__ import annotations
from typing import Type
import subprocess
import contextlib
import tempfile
import shutil
import os
from .machine import Machine


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
        self.update(path)

    def update(self, destdir: str):
        with Machine(f"maint-{self.__class__.__name__.lower()}", destdir, ephemeral=False) as machine:
            self.run_update(machine)

    def run_update(self, machine: Machine):
        """
        Run update or regular maintenance commands on the given machine
        """
        raise NotImplementedError(f"{self.__class__}.run_update not implemented")

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

    def run_update(self, machine: Machine):
        machine.run(["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/yum.conf"])
        for pkg in ["epel-release", "@buildsys-build", "yum-utils", "git", "rpmdevtools"]:
            machine.run(["/usr/bin/yum", "install", "-y", pkg])
        machine.run(["/usr/bin/yum", "install", "-q" "-y", "yum-plugin-copr"])
        machine.run(["/usr/bin/yum", "copr", "enable", "-q" "-y", "simc/stable", "epel-7"])
        machine.run(["/usr/bin/yum", "upgrade", "-q", "-y"])


@Distro.register
class Centos8(Rpm):
    BASEURL = "http://mirror.centos.org/centos-8/8/BaseOS/$basearch/os"
    RELEASEVER = 8
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "git", "dbus"]

    def run_update(self, machine: Machine):
        machine.run(["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/dnf/dnf.conf"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "epel-release"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(config-manager)"])
        machine.run(["/usr/bin/dnf", "config-manager", "--set-enabled", "powertools"])
        machine.run(["/usr/bin/dnf", "groupinstall", "-q", "-y", "Development Tools"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(builddep)"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "git"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "rpmdevtools"])
        machine.run(["/usr/bin/dnf", "copr", "enable", "-y", "simc/stable"])
        machine.run(["/usr/bin/dnf", "upgrade", "-q", "-y"])


class Fedora(Rpm):
    def run_update(self, machine: Machine):
        machine.run(["/usr/bin/rpmdb", "--rebuilddb"])
        machine.run(["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/dnf/dnf.conf"])
        machine.run(["/usr/bin/dnf", "install", "-y", "--allowerasing", "@buildsys-build"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(builddep)"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "git"])
        machine.run(["/usr/bin/dnf", "install", "-q", "-y", "rpmdevtools"])
        machine.run(["/usr/bin/dnf", "copr", "enable", "-y", "simc/stable"])
        machine.run(["/usr/bin/dnf", "upgrade", "-q", "-y"])


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
