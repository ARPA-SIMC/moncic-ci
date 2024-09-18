from __future__ import annotations

import contextlib
import glob
import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from typing import TYPE_CHECKING

from ..container import ContainerConfig
from ..utils.fs import atomic_writer
from .distro import Distro, DistroFamily

if TYPE_CHECKING:
    from ..system import System

log = logging.getLogger(__name__)


@DistroFamily.register
class Fedora(DistroFamily):
    VERSIONS = (32, 33, 34, 35, 36, 37, 38, 39, 40)
    SHORTCUTS = {f"fedora{version}": f"fedora:{version}" for version in VERSIONS}

    def create_distro(self, version: str) -> Distro:
        intver = int(version)
        if intver in self.VERSIONS:
            return FedoraDistro(f"fedora:{intver}", intver)
        else:
            raise KeyError(f"Fedora version {version!r} is not (yet) supported")


@DistroFamily.register
class Rocky(DistroFamily):
    VERSIONS = (8, 9)
    SHORTCUTS = {f"rocky{version}": f"rocky:{version}" for version in VERSIONS}

    def create_distro(self, version: str) -> Distro:
        major = int(version.split(".")[0])
        if major in self.VERSIONS:
            return RockyDistro(f"rocky:{major}", major)
        else:
            raise KeyError(f"Rocky version {version!r} is not (yet) supported")


@DistroFamily.register
class Centos(DistroFamily):
    VERSIONS = (7, 8)
    SHORTCUTS = {f"centos{version}": f"centos:{version}" for version in VERSIONS}

    def create_distro(self, version: str) -> Distro:
        intver = int(version)
        if intver == 7:
            return Centos7(f"centos:{intver}")
        elif intver == 8:
            return Centos8(f"centos:{intver}")
        else:
            raise KeyError(f"Centos version {version!r} is not (yet) supported")


class RpmDistro(Distro):
    """
    Common implementation for rpm-based distributions
    """

    version: int

    def get_base_packages(self) -> list[str]:
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
                installer,
                "-c",
                dnf_config,
                "-y",
                "-q",
                "--disablerepo=*",
                "--enablerepo=chroot-base",
                "--disableplugin=*",
                f"--installroot={installroot}",
                f"--releasever={self.version}",
                "install",
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
                        raise RuntimeError(
                            f"/var/lib/rpm in installed system points to {system_rpmdb}" " which is outside installroot"
                        )
                shutil.rmtree(system_rpmdb)
                shutil.move(private_rpmdb, system_rpmdb)
            with system.create_container(config=ContainerConfig(ephemeral=False)) as container:
                container.run(["/usr/bin/rpmdb", "--rebuilddb"])


class YumDistro(RpmDistro):
    def get_base_packages(self) -> list[str]:
        return super().get_base_packages() + ["yum"]

    def get_update_pkgdb_script(self, system: System):
        res = super().get_update_pkgdb_script(system)
        res.append(["/usr/bin/yum", "updateinfo", "-q", "-y"])
        return res

    def get_upgrade_system_script(self, system: System) -> list[list[str]]:
        res = super().get_upgrade_system_script(system)
        res.append(["/usr/bin/yum", "upgrade", "-q", "-y"])
        return res

    def get_install_packages_script(self, system: System, packages: list[str]) -> list[list[str]]:
        res = super().get_install_packages_script(system, packages)
        res.append(["/usr/bin/yum", "install", "-q", "-y"] + packages)
        return res


class DnfDistro(RpmDistro):
    def get_base_packages(self) -> list[str]:
        return super().get_base_packages() + ["dnf"]

    def get_setup_network_script(self, system: System):
        res = super().get_setup_network_script(system)
        res.append(["/usr/bin/systemctl", "mask", "--now", "systemd-resolved"])
        return res

    def get_update_pkgdb_script(self, system: System):
        res = super().get_update_pkgdb_script(system)
        res.append(["/usr/bin/dnf", "updateinfo", "-q", "-y"])
        return res

    def get_upgrade_system_script(self, system: System) -> list[list[str]]:
        res = super().get_upgrade_system_script(system)
        res.append(["/usr/bin/dnf", "upgrade", "-q", "-y"])
        return res

    def get_install_packages_script(self, system: System, packages: list[str]):
        res = super().get_install_packages_script(system, packages)
        res.append(["/usr/bin/dnf", "install", "-q", "-y"] + packages)
        return res

    def get_versions(self, packages: list[str]) -> dict[str, dict[str, str]]:
        # We cannot just import dnf here, as it is unlikely to match the
        # version of python in the host system. We therefore shell out to the
        # guest system's version of python
        script = f"""#!/usr/bin/python3
import json
import sys
from collections import defaultdict

import dnf

requirements = {packages!r}
res = defaultdict(dict)

base = dnf.Base()
base.read_all_repos()
base.fill_sack()
for requirement in requirements:
    for pkg in base.sack.query().available().latest().filter(
            provides=requirement, arch=base.conf.substitutions["arch"]):
        res[requirement][pkg.name] = pkg.version

json.dump(res, sys.stdout)
"""

        with open("/tmp/script", "wt") as fd:
            fd.write(script)
        res = subprocess.run(["/usr/bin/python3", "/tmp/script"], stdout=subprocess.PIPE, text=True, check=True)
        return json.loads(res.stdout)


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
            with open(fn) as fd:
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
