from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from typing import TYPE_CHECKING

import requests

from ..container import BindConfig, ContainerConfig
from .distro import Distro, DistroFamily, DistroInfo

if TYPE_CHECKING:
    from moncic.nspawn.system import NspawnSystem


@DistroFamily.register
class Debian(DistroFamily):
    VERSION_IDS = {
        "8": "jessie",
        "9": "stretch",
        "10": "buster",
        "11": "bullseye",
        "12": "bookworm",
        "13": "trixie",
    }
    EXTRA_SUITES = ("oldstable", "stable", "testing", "unstable")
    SHORTCUTS = {suite: f"debian:{suite}" for suite in list(VERSION_IDS.values()) + ["sid"]}

    def create_distro(self, version: str) -> Distro:
        # Map version numbers to release codenames
        suite = self.VERSION_IDS.get(version, version)

        if suite in self.SHORTCUTS or suite in self.EXTRA_SUITES:
            return DebianDistro(f"debian:{version}", suite)
        else:
            raise KeyError(f"Debian version {version!r} is not (yet) supported")

    def list_distros(self) -> list[DistroInfo]:
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

        return [DistroInfo(name, shortcuts) for name, shortcuts in by_name.items()]


@DistroFamily.register
class Ubuntu(DistroFamily):
    VERSION_IDS = {
        "16.04": "xenial",
        "18.04": "bionic",
        "20.04": "focal",
        "21.04": "hirsute",
        "21.10": "impish",
        "22.04": "jammy",
        "22.10": "kinetic",
        "23.04": "lunar",
        "23.10": "mantic",
        "24.04": "noble",
    }
    SHORTCUTS = {suite: f"ubuntu:{suite}" for suite in ("xenial", "bionic", "focal", "hirsute", "impish", "jammy")}
    LEGACY = ("xenial",)

    def create_distro(self, version: str) -> Distro:
        # Map version numbers to release codenames
        suite = self.VERSION_IDS.get(version, version)

        if suite in self.SHORTCUTS:
            return UbuntuDistro(f"ubuntu:{version}", suite)
        else:
            raise KeyError(f"Ubuntu version {version!r} is not (yet) supported")

    def list_distros(self) -> list[DistroInfo]:
        """
        Return a list of distros available in this family
        """
        by_name = defaultdict(list)
        for suite, name in self.SHORTCUTS.items():
            by_name[name].append(suite)
        for vid, suite in self.VERSION_IDS.items():
            by_name[f"ubuntu:{suite}"].append(f"ubuntu:{vid}")

        return [DistroInfo(name, shortcuts) for name, shortcuts in by_name.items()]


class DebianDistro(Distro):
    """
    Common implementation for Debian-based distributions
    """

    APT_INSTALL_CMD = [
        "/usr/bin/apt-get",
        "--assume-yes",
        "--quiet",
        "--show-upgraded",
        # The space after -o is odd but required, and I could
        # not find a better working syntax
        '-o Dpkg::Options::="--force-confnew"',
    ]

    def __init__(self, name: str, suite: str, mirror: str = "http://deb.debian.org/debian"):
        super().__init__(name)
        self.mirror = mirror
        self.suite = suite

    def container_config_hook(self, system: NspawnSystem, config: ContainerConfig):
        super().container_config_hook(system, config)
        if apt_archive_path := system.images.session.apt_archives:
            config.binds.append(BindConfig.create(apt_archive_path, "/var/cache/apt/archives", "aptcache"))

        if extra_packages_dir := system.images.session.extra_packages_dir:
            config.binds.append(BindConfig.create(extra_packages_dir, "/srv/moncic-ci/mirror/packages", "aptpackages"))

    def get_base_packages(self) -> list[str]:
        res = super().get_base_packages()
        res += ["systemd", "apt-utils", "eatmydata", "iproute2"]
        return res

    def get_gbp_branches(self) -> list[str]:
        """
        Return the default git-buildpackage debian-branch name for this
        distribution
        """
        if self.suite in ("unstable", "sid"):
            return ["debian/unstable", "debian/sid", "debian/latest"]
        else:
            return ["debian/" + self.suite, "debian/latest"]

    def bootstrap(self, system: NspawnSystem):
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
                    check=True,
                )
                cmd.append(f"--keyring={tmpfile.name}")

            cmd += [self.suite, installroot, self.mirror]
            # If eatmydata is available, we can use it to make deboostrap significantly faster
            eatmydata = shutil.which("eatmydata")
            if eatmydata is not None:
                cmd.insert(0, eatmydata)
            system.local_run(cmd)

    def get_update_pkgdb_script(self, system: NspawnSystem):
        res = super().get_update_pkgdb_script(system)
        res.append(["/usr/bin/apt-get", "update"])
        return res

    def get_upgrade_system_script(self, system: NspawnSystem) -> list[list[str]]:
        res = super().get_upgrade_system_script(system)
        res.append(self.APT_INSTALL_CMD + ["full-upgrade"])
        return res

    def get_install_packages_script(self, system: NspawnSystem, packages: list[str]) -> list[list[str]]:
        res = super().get_install_packages_script(system, packages)
        res.append(self.APT_INSTALL_CMD + ["satisfy"] + packages)
        return res

    def get_versions(self, packages: list[str]) -> dict[str, dict[str, str]]:
        re_inst = re.compile(r"^Inst (\S+) \((\S+)")
        cmd_prefix = [
            "apt-get",
            "satisfy",
            "-s",
            "-o",
            "Dir::state::status=/dev/null",
            "-o",
            "APT::Build-Essential=,",
            "-o",
            "APT::Get::Show-Versions=true",
        ]

        # Get a list of packages that would be installed as build-essential
        base: set[str] = set()
        res = subprocess.run(cmd_prefix + ["build-essential"], stdout=subprocess.PIPE, check=True, text=True)
        for line in res.stdout.splitlines():
            if mo := re_inst.match(line):
                base.add(mo.group(1))

        result: dict[str, dict[str, str]] = defaultdict(dict)

        # Get a list of packages that would be installed when the given package
        # list is installed
        for requirement in packages:
            if requirement == "build-essential":
                continue
            res = subprocess.run(cmd_prefix + [requirement], stdout=subprocess.PIPE, check=True, text=True)
            for line in res.stdout.splitlines():
                if mo := re_inst.match(line):
                    if (name := mo.group(1)) not in base:
                        result[requirement][name] = mo.group(2)

        common = set.intersection(*(set(v.keys()) for v in result.values()))
        for v in result.values():
            for name in common:
                del v[name]

        return result


class UbuntuDistro(DebianDistro):
    """
    Common implementation for Ubuntu-based distributions
    """

    def __init__(self, name: str, suite: str, mirror: str = "http://archive.ubuntu.com/ubuntu/"):
        super().__init__(name, suite, mirror=mirror)

    def get_gbp_branches(self) -> list[str]:
        """
        Return the default git-buildpackage debian-branch name for this
        distribution
        """
        return ["ubuntu/" + self.suite, "ubuntu/latest", "debian/latest"]
