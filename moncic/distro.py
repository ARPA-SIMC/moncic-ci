from __future__ import annotations
from typing import Type, Optional, List
import subprocess
import contextlib
import tempfile
import shutil
import os
from .machine import Machine, LegacyMachine
from .osrelease import parse_osrelase


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

    def machine(self, ostree: str, name: Optional[str] = None, ephemeral: bool = True) -> Machine:
        """
        Create a Machine to run this distro
        """
        return Machine(ostree, name, ephemeral)

    def update(self, ostree: str):
        with self.machine(ostree, f"maint-{self.__class__.__name__.lower()}", ephemeral=False) as machine:
            self.run_update(machine)

    def run_update(self, machine: Machine):
        """
        Run update or regular maintenance commands on the given machine
        """
        raise NotImplementedError(f"{self.__class__}.run_update not implemented")

    @contextlib.contextmanager
    def checkout(self, repo: Optional[str] = None):
        if repo is None:
            yield None
        else:
            with tempfile.TemporaryDirectory() as workdir:
                # Git checkout in a temporary directory
                subprocess.run(
                        ["git", "clone", repo],
                        cwd=workdir, check=True)
                # Look for the directory that git created
                names = os.listdir(workdir)
                if len(names) != 1:
                    raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")
                yield os.path.join(workdir, names[0])

    def run_shell(self, ostree: str, ephemeral: bool = True, checkout: Optional[str] = None):
        """
        Open a shell on the given ostree
        """
        def escape_bind_ro(s: str):
            r"""
            Escape a path for use in systemd-nspawn --bind-ro.

            Man systemd-nspawn says:

              Backslash escapes are interpreted, so "\:" may be used to embed
              colons in either path.
            """
            return s.replace(":", r"\:")

        with self.checkout(checkout) as repo_path:
            cmd = ["systemd-nspawn", "-D", ostree]
            if ephemeral:
                cmd.append("--ephemeral")

            if repo_path is not None:
                name = os.path.basename(repo_path)
                if name.startswith("."):
                    raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")

                cmd.append(f"--bind={escape_bind_ro(repo_path)}:/root/{escape_bind_ro(name)}")
                cmd.append(f"--chdir=/root/{name}")

            subprocess.run(cmd, check=True)

    @classmethod
    def register(cls, distro_cls: Type["Distro"]) -> Type["Distro"]:
        cls.distros[distro_cls.__name__.lower()] = distro_cls
        return distro_cls

    @classmethod
    def list(cls) -> List[str]:
        return list(cls.distros.keys())

    @classmethod
    def create(cls, name: str) -> "Distro":
        distro_cls = cls.distros[name]
        return distro_cls()

    @classmethod
    def from_ostree(cls, ostree: str) -> "Distro":
        """
        Instantiate a Distro from an existing filesystem tree
        """
        info = parse_osrelase(os.path.join(ostree, "etc", "os-release"))
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
    PACKAGES = ["bash", "vim-minimal", "yum", "rootfiles", "dbus"]

    def run_update(self, machine: Machine):
        machine.run(["/usr/bin/sed", "-i", "/^tsflags=/d", "/etc/yum.conf"])
        for pkg in ["epel-release", "@buildsys-build", "yum-utils", "git", "rpmdevtools"]:
            machine.run(["/usr/bin/yum", "install", "-y", pkg])
        machine.run(["/usr/bin/yum", "install", "-q" "-y", "yum-plugin-copr"])
        machine.run(["/usr/bin/yum", "copr", "enable", "-q" "-y", "simc/stable", "epel-7"])
        machine.run(["/usr/bin/yum", "upgrade", "-q", "-y"])

    def machine(self, ostree: str, name: Optional[str] = None, ephemeral: bool = True) -> Machine:
        """
        Create a Machine to run this distro
        """
        return LegacyMachine(ostree, name, ephemeral)


@Distro.register
class Centos8(Rpm):
    BASEURL = "http://mirror.centos.org/centos-8/8/BaseOS/$basearch/os"
    RELEASEVER = 8
    PACKAGES = ["bash", "vim-minimal", "dnf", "rootfiles", "dbus"]

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
