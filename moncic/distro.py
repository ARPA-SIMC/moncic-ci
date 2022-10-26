from __future__ import annotations
from collections import defaultdict
import contextlib
import glob
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from typing import Optional, Type, List, Dict, Iterable, NamedTuple, TYPE_CHECKING

from .osrelease import parse_osrelase
from .container import BindConfig, ContainerConfig
from .utils import atomic_writer
if TYPE_CHECKING:
    from .system import System

import requests

log = logging.getLogger(__name__)


class DistroInfo(NamedTuple):
    """
    Information about a distribution
    """
    # Canonical name
    name: str
    shortcuts: List[str]


class DistroFamily:
    """
    Base class for handling a family of distributions
    """
    # Registry of known families
    families: Dict[str, DistroFamily] = {}

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
    def list(cls) -> Iterable[DistroFamily]:
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
        # For os-release format documentation, see
        # https://www.freedesktop.org/software/systemd/man/os-release.html

        # TODO: check if "{path}.yaml" exists
        info: Optional[Dict[str, str]]
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

    def list_distros(self) -> List[DistroInfo]:
        """
        Return a list of distros available in this family
        """
        return [
            DistroInfo(name, [shortcut])
            for shortcut, name in self.SHORTCUTS.items()]


@DistroFamily.register
class Debian(DistroFamily):
    VERSION_IDS = {
        "8": "jessie",
        "9": "stretch",
        "10": "buster",
        "11": "bullseye",
        "12": "bookworm",
    }
    EXTRA_SUITES = ("oldstable", "stable", "testing", "unstable")
    SHORTCUTS = {
        suite: f"debian:{suite}"
        for suite in list(VERSION_IDS.values()) + ["sid"]
    }

    def create_distro(self, version: str) -> "Distro":
        # Map version numbers to release codenames
        suite = self.VERSION_IDS.get(version, version)

        if suite in self.SHORTCUTS or suite in self.EXTRA_SUITES:
            return DebianDistro(f"debian:{version}", suite)
        else:
            raise KeyError(f"Debian version {version!r} is not (yet) supported")

    def list_distros(self) -> List[DistroInfo]:
        """
        Return a list of distros available in this family
        """
        by_name = defaultdict(list)
        for suite, name in self.SHORTCUTS.items():
            by_name[name].append(suite)
        for vid, suite in self.VERSION_IDS.items():
            by_name[f"debian:{suite}"].append(f"debian:{vid}")
        for alias in self.EXTRA_SUITES:
            by_name[f"debian:{alias}"]

        return [
            DistroInfo(name, shortcuts)
            for name, shortcuts in by_name.items()]


@DistroFamily.register
class Ubuntu(DistroFamily):
    VERSION_IDS = {
        "16.04": "xenial",
        "18.04": "bionic",
        "20.04": "focal",
        "21.04": "hirsute",
        "21.10": "impish",
        "22.04": "jammy",
    }
    SHORTCUTS = {
        suite: f"ubuntu:{suite}"
        for suite in ("xenial", "bionic", "focal", "hirsute", "impish", "jammy")
    }
    LEGACY = ("xenial",)

    def create_distro(self, version: str) -> "Distro":
        # Map version numbers to release codenames
        suite = self.VERSION_IDS.get(version, version)

        if suite in self.SHORTCUTS:
            return UbuntuDistro(f"ubuntu:{version}", suite)
        else:
            raise KeyError(f"Ubuntu version {version!r} is not (yet) supported")

    def list_distros(self) -> List[DistroInfo]:
        """
        Return a list of distros available in this family
        """
        by_name = defaultdict(list)
        for suite, name in self.SHORTCUTS.items():
            by_name[name].append(suite)
        for vid, suite in self.VERSION_IDS.items():
            by_name[f"ubuntu:{suite}"].append(f"ubuntu:{vid}")

        return [
            DistroInfo(name, shortcuts)
            for name, shortcuts in by_name.items()]


@DistroFamily.register
class Fedora(DistroFamily):
    VERSIONS = (32, 33, 34, 35, 36)
    SHORTCUTS = {
        f"fedora{version}": f"fedora:{version}"
        for version in VERSIONS
    }

    def create_distro(self, version: str) -> "Distro":
        intver = int(version)
        if intver in self.VERSIONS:
            return FedoraDistro(f"fedora:{intver}", intver)
        else:
            raise KeyError(f"Fedora version {version!r} is not (yet) supported")


@DistroFamily.register
class Rocky(DistroFamily):
    VERSIONS = (8, 9)
    SHORTCUTS = {
        f"rocky{version}": f"rocky:{version}"
        for version in VERSIONS
    }

    def create_distro(self, version: str) -> "Distro":
        major = int(version.split(".")[0])
        if major in self.VERSIONS:
            return RockyDistro(f"rocky:{major}", major)
        else:
            raise KeyError(f"Rocky version {version!r} is not (yet) supported")


@DistroFamily.register
class Centos(DistroFamily):
    VERSIONS = (7, 8)
    SHORTCUTS = {
        f"centos{version}": f"centos:{version}"
        for version in VERSIONS
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
    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return self.name

    def get_base_packages(self) -> List[str]:
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

    def get_update_script(self) -> List[List[str]]:
        """
        Get the sequence of commands to use for regular update/maintenance
        """
        return []


class RpmDistro(Distro):
    """
    Common implementation for rpm-based distributions
    """
    version: int

    def get_base_packages(self) -> List[str]:
        res = super().get_base_packages()
        res += ["rootfiles", "iproute"]
        return res

    @contextlib.contextmanager
    def chroot_config(self):
        baseurl = self.baseurl.format(mirror=self.mirror)

        with tempfile.NamedTemporaryFile("wt", suffix=".repo") as fd:
            print("[chroot-base]", file=fd)
            print("name=Linux $releasever - $basearch", file=fd)
            print(f"baseurl={baseurl}", file=fd)
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
                installer, "-c", dnf_config, "-y", "-q", "--disablerepo=*",
                "--enablerepo=chroot-base", "--disableplugin=*",
                f"--installroot={installroot}", f"--releasever={self.version}",
                "install"
            ] + self.get_base_packages()

            # If eatmydata is available, we can use it to make boostrap significantly faster
            #
            # Disabled for now, this causes noise when dnf executes scriptlets
            # inside the target system
            #
            # eatmydata = shutil.which("eatmydata")
            # if eatmydata is not None:
            #     cmd.insert(0, eatmydata)

            system.local_run(cmd)

            # If dnf used a private rpmdb, promote it as the rpmdb of the newly
            # created system. See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1004863#32
            private_rpmdb = os.path.join(installroot, "root", ".rpmdb")
            system_rpmdb = os.path.join(installroot, "var", "lib", "rpm")
            if os.path.isdir(private_rpmdb):
                log.info("Moving %r to %r", private_rpmdb, system_rpmdb)
                if os.path.islink(system_rpmdb):
                    system_rpmdb = os.path.realpath(system_rpmdb)
                    if installroot not in os.path.commonprefix((system_rpmdb, installroot)):
                        raise RuntimeError(f"/var/lib/rpm in installed system points to {system_rpmdb}"
                                           " which is outside installroot")
                shutil.rmtree(system_rpmdb)
                shutil.move(private_rpmdb, system_rpmdb)
            with system.create_container(config=ContainerConfig(ephemeral=False)) as container:
                container.run(["/usr/bin/rpmdb", "--rebuilddb"])


class YumDistro(RpmDistro):
    def get_base_packages(self) -> List[str]:
        return super().get_base_packages() + ["yum"]

    def get_update_script(self):
        res = super().get_update_script()
        return res + [
            ["/usr/bin/yum", "upgrade", "-q", "-y"],
            ["/usr/bin/yum", "install", "-q", "-y"] + self.get_base_packages(),
        ]


class DnfDistro(RpmDistro):
    def get_base_packages(self) -> List[str]:
        return super().get_base_packages() + ["dnf"]

    def get_update_script(self):
        res = super().get_update_script()
        return res + [
            ["/usr/bin/systemctl", "mask", "--now", "systemd-resolved"],
            ["/usr/bin/dnf", "upgrade", "-q", "-y"],
            ["/usr/bin/dnf", "install", "-q", "-y"] + self.get_base_packages(),
        ]


class Centos7(YumDistro):
    mirror = "http://mirror.centos.org"
    baseurl = "{mirror}/centos/7/os/$basearch"
    version = 7

    def bootstrap(self, system: System):
        super().bootstrap(system)
        installroot = os.path.abspath(system.path)
        varsdir = os.path.join(installroot, "etc", "yum", "vars")
        os.makedirs(varsdir, exist_ok=True)
        with open(os.path.join(varsdir, "releasever"), "wt") as fd:
            print("7", file=fd)


class Centos8(DnfDistro):
    mirror = "https://vault.centos.org"
    baseurl = "{mirror}/centos/8/BaseOS//$basearch/os/"
    version = 8

    def bootstrap(self, system: System):
        super().bootstrap(system)
        # self.system.local_run(["tar", "-C", self.system.path, "-zxf", "images/centos8.tar.gz"])
        # Fixup repository information to point at the vault
        for fn in glob.glob(os.path.join(system.path, "etc/yum.repos.d/CentOS-*")):
            log.info("Updating %r to point mirrors to the Vault", fn)
            with open(fn, "rt") as fd:
                st = os.stat(fd.fileno())
                with atomic_writer(fn, mode="wt", chmod=stat.S_IMODE(st.st_mode)) as tf:
                    for line in fd:
                        if line.startswith("mirrorlist="):
                            print(f"#{line}", file=tf)
                        elif line.startswith("#baseurl=http://mirror.centos.org"):
                            print(f"baseurl=http://vault.centos.org{line[33:]}", file=tf)
                        else:
                            print(line, file=tf)
                    tf.flush()


class FedoraDistro(DnfDistro):
    mirror = "http://download.fedoraproject.org"

    def __init__(self, name: str, version: int):
        super().__init__(name)
        self.version = version
        self.baseurl = f"{self.mirror}/pub/fedora/linux/releases/{version}/Everything/$basearch/os/"


class RockyDistro(DnfDistro):
    mirror = "http://dl.rockylinux.org"

    def __init__(self, name: str, version: int):
        super().__init__(name)
        self.version = version
        self.baseurl = f"{self.mirror}/pub/rocky/{version}/BaseOS/$basearch/os/"


class DebianDistro(Distro):
    """
    Common implementation for Debian-based distributions
    """
    def __init__(self, name: str, suite: str, mirror: str = "http://deb.debian.org/debian"):
        super().__init__(name)
        self.mirror = mirror
        self.suite = suite

    def container_config_hook(self, system: System, config: ContainerConfig):
        super().container_config_hook(system, config)
        if system.images.session.moncic.config.deb_cache_dir is not None:
            config.binds.append(BindConfig.create(
                system.images.session.apt_archives(),
                "/var/cache/apt/archives",
                "aptcache"))

        if (extra_packages_dir := system.images.session.extra_packages_dir()):
            config.binds.append(BindConfig.create(
                extra_packages_dir,
                "/srv/moncic-ci/mirror/packages",
                "aptpackages"))

    def get_base_packages(self) -> List[str]:
        res = super().get_base_packages()
        res += ["systemd", "apt-utils", "eatmydata", "iproute2"]
        return res

    def get_gbp_branch(self) -> str:
        """
        Return the default git-buildpackage debian-branch name for this
        distribution
        """
        return "debian/" + self.suite

    def bootstrap(self, system: System):
        with contextlib.ExitStack() as stack:
            installroot = os.path.abspath(system.path)
            cmd = ["debootstrap", "--include=" + ",".join(self.get_base_packages()), "--variant=minbase"]

            # TODO: use version to fetch the key, to make this generic
            # TODO: add requests and gpg to dependencies
            if self.suite == "jessie":
                tmpfile = stack.enter_context(tempfile.NamedTemporaryFile(suffix=".gpg"))
                res = requests.get("https://ftp-master.debian.org/keys/release-8.asc")
                res.raise_for_status()
                subprocess.run(
                        ["gpg", "--import", "--no-default-keyring", "--keyring", tmpfile.name],
                        input=res.content,
                        check=True)
                cmd.append(f"--keyring={tmpfile.name}")

            cmd += [self.suite, installroot, self.mirror]
            # If eatmydata is available, we can use it to make deboostrap significantly faster
            eatmydata = shutil.which("eatmydata")
            if eatmydata is not None:
                cmd.insert(0, eatmydata)
            system.local_run(cmd)

    def get_update_script(self) -> List[List[str]]:
        """
        Get the sequence of commands to use for regular update/maintenance
        """
        apt_install_cmd = [
                "/usr/bin/apt-get", "--assume-yes", "--quiet", "--show-upgraded",
                # The space after -o is odd but required, and I could
                # not find a better working syntax
                '-o Dpkg::Options::="--force-confnew"']
        return [
            ["/usr/bin/apt-get", "update"],
            apt_install_cmd + ["full-upgrade"],
            apt_install_cmd + ["install"] + self.get_base_packages(),
        ]


class UbuntuDistro(DebianDistro):
    """
    Common implementation for Ubuntu-based distributions
    """
    def __init__(self, name: str, suite: str, mirror: str = "http://archive.ubuntu.com/ubuntu/"):
        super().__init__(name, suite, mirror=mirror)

    def get_gbp_branch(self) -> str:
        """
        Return the default git-buildpackage debian-branch name for this
        distribution
        """
        return "ubuntu/" + self.suite
