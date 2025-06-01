from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Collection
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, override, Any

import requests

from moncic.container import BindConfig, BindType, ContainerConfig
from moncic.utils.script import Script

from .distro import Distro, DistroFamily

if TYPE_CHECKING:
    from moncic.image import Image
    from moncic.images import Images


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

    def __init__(
        self,
        family: DistroFamily,
        name: str,
        version: str | None,
        other_names: list[str] | None = None,
        mirror: str = "http://deb.debian.org/debian",
        key_url: str | None = None,
        cgroup_v1: bool = False,
        bootstrappers: Collection[str] = ("mmdebstrap", "debootstrap"),
    ):
        super().__init__(family, name, version, other_names, cgroup_v1=cgroup_v1)
        self.mirror = mirror
        self.key_url = key_url
        self.bootstrappers = list(bootstrappers)

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("docker.io/library/debian", self.name)

    @override
    def container_config_hook(self, image: Image, config: ContainerConfig) -> None:
        super().container_config_hook(image, config)
        if apt_archive_path := image.session.apt_archives:
            config.binds.append(BindConfig.create(apt_archive_path, "/var/cache/apt/archives", BindType.APTCACHE))

        if extra_packages_dir := image.session.extra_packages_dir:
            config.binds.append(
                BindConfig.create(extra_packages_dir, "/srv/moncic-ci/mirror/packages", BindType.APTPACKAGES)
            )

    @override
    def get_base_packages(self) -> list[str]:
        res = super().get_base_packages()
        res += ["systemd", "apt-utils", "eatmydata", "iproute2"]
        return res

    def get_gbp_branches(self) -> list[str]:
        """
        Return the default git-buildpackage debian-branch name for this
        distribution
        """
        if self.name in ("unstable", "sid"):
            return ["debian/unstable", "debian/sid", "debian/latest"]
        else:
            return ["debian/" + self.name, "debian/latest"]

    @override
    def bootstrap(self, images: Images, path: Path) -> None:
        for name in self.bootstrappers:
            if bootstrapper := shutil.which(name):
                break
        else:
            raise RuntimeError("No debian bootstrapper found. Tried: {', '.join(bootstrappers)}")
        with contextlib.ExitStack() as stack:
            installroot = path.absolute()
            cmd = [bootstrapper, "--include=" + ",".join(self.get_base_packages()), "--variant=minbase"]

            if self.key_url is not None:
                tmpfile = stack.enter_context(tempfile.NamedTemporaryFile(suffix=".gpg"))
                res = requests.get(self.key_url)
                res.raise_for_status()
                subprocess.run(
                    ["gpg", "--import", "--no-default-keyring", "--keyring", tmpfile.name],
                    input=res.content,
                    check=True,
                )
                cmd.append(f"--keyring={tmpfile.name}")

            cmd += [self.name, installroot.as_posix(), self.mirror]
            # If eatmydata is available, we can use it to make deboostrap significantly faster
            eatmydata = shutil.which("eatmydata")
            if eatmydata is not None:
                cmd.insert(0, eatmydata)
            images.host_run(cmd)

    @override
    def get_update_pkgdb_script(self, script: Script) -> None:
        super().get_update_pkgdb_script(script)
        script.run(["/usr/bin/apt-get", "update"])

    @override
    def get_upgrade_system_script(self, script: Script) -> None:
        super().get_upgrade_system_script(script)
        script.run(self.APT_INSTALL_CMD + ["full-upgrade"])

    @override
    def get_install_packages_script(self, script: Script, packages: list[str]) -> None:
        super().get_install_packages_script(script, packages)
        script.run(self.APT_INSTALL_CMD + ["satisfy"] + packages)

    @override
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

    def __init__(self, family: DistroFamily, name: str, version: str, archived: bool = False, **kwargs: Any) -> None:
        if archived:
            mirror = "https://old-releases.ubuntu.com/ubuntu/"
        else:
            mirror = "https://archive.ubuntu.com/ubuntu/"
        super().__init__(family, name, version, mirror=mirror, **kwargs)

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("docker.io/library/ubuntu", self.name)

    @override
    def get_gbp_branches(self) -> list[str]:
        """
        Return the default git-buildpackage debian-branch name for this
        distribution
        """
        return ["ubuntu/" + self.name, "ubuntu/latest", "debian/latest"]


class Debian(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(
            DebianDistro(
                self,
                "jessie",
                "8",
                mirror="http://archive.debian.org/debian/",
                key_url="https://ftp-master.debian.org/keys/release-8.asc",
                cgroup_v1=True,
            )
        )
        self.add_distro(
            DebianDistro(
                self,
                "stretch",
                "9",
                mirror="http://archive.debian.org/debian/",
                key_url="https://ftp-master.debian.org/keys/release-9.asc",
            )
        )
        self.add_distro(DebianDistro(self, "buster", "10", ["oldoldstable"]))
        self.add_distro(DebianDistro(self, "bullseye", "11", ["oldstable"]))
        self.add_distro(DebianDistro(self, "bookworm", "12", ["stable"]))
        self.add_distro(DebianDistro(self, "trixie", "13"))
        # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1021663
        # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1077764
        self.add_distro(DebianDistro(self, "testing", None))
        self.add_distro(DebianDistro(self, "sid", None, ["unstable"]))

    @override
    def distro_from_osrelease(self, info: dict[str, str], fallback_name: str) -> "Distro":
        # Distinguishing testing from sid is... complicated. See:
        #
        # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1021663
        # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1077764

        # If VERSION_ID is not set, then we can be in testing or sid. As we
        # cannot tell which without ugly hacks, we assume sid as for a CI that
        # is the common case.

        # Note that in the few months before a release, Debian's sid and
        # testing will identify as the coming stable instead.

        # If one needs to target testing or sid explicitly, one can do so by
        # creating a YAML configuration file for the image

        if (os_version := info.get("VERSION_ID")) is None:
            os_version = "sid"

        names: list[str] = [f"{self.name}:{os_version}"]
        if "." in os_version:
            names.append(f"{self.name}:{os_version.split(".")[0]}")

        for name in names:
            if res := self.distro_lookup.get(name):
                return res

        raise KeyError(
            f"Distro ID={self.name!r}, VERSION_ID={os_version!r} not found."
            f" Tried: {', '.join(repr(name) for name in names)} "
        )


class Ubuntu(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(UbuntuDistro(self, "xenial", "16.04", cgroup_v1=True, bootstrappers=["debootstrap"]))
        self.add_distro(UbuntuDistro(self, "bionic", "18.04"))
        self.add_distro(UbuntuDistro(self, "focal", "20.04"))
        self.add_distro(UbuntuDistro(self, "hirsute", "21.04", archived=True))
        self.add_distro(UbuntuDistro(self, "impish", "21.10", archived=True))
        self.add_distro(UbuntuDistro(self, "jammy", "22.04"))
        self.add_distro(UbuntuDistro(self, "kinetic", "22.10", archived=True))
        self.add_distro(UbuntuDistro(self, "lunar", "23.04", archived=True))
        self.add_distro(UbuntuDistro(self, "mantic", "23.10", archived=True))
        self.add_distro(UbuntuDistro(self, "noble", "24.04"))
        self.add_distro(UbuntuDistro(self, "oracular", "24.10"))
        self.add_distro(UbuntuDistro(self, "plucky", "25.04"))
