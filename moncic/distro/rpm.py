from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from moncic.utils.fs import atomic_writer
from moncic.utils.script import Script

from .distro import Distro, DistroFamily

if TYPE_CHECKING:
    from moncic.images import Images


class RpmDistro(Distro):
    """
    Common implementation for rpm-based distributions
    """

    baseurl: str
    mirror: str

    @override
    def get_base_packages(self) -> list[str]:
        res = super().get_base_packages()
        res += ["rootfiles", "iproute"]
        return res

    @contextlib.contextmanager
    def chroot_config(self) -> Generator[str]:
        baseurl = self.baseurl.format(mirror=self.mirror)

        with tempfile.NamedTemporaryFile("wt", suffix=".repo") as fd:
            print("[chroot-base]", file=fd)
            print("name=Linux $releasever - $basearch", file=fd)
            print(f"baseurl={baseurl}", file=fd)
            print("enabled=1", file=fd)
            print("gpgcheck=0", file=fd)
            fd.flush()
            yield fd.name

    @override
    def bootstrap(self, images: Images, path: Path) -> None:
        installer = shutil.which("dnf")
        if installer is None:
            installer = shutil.which("yum")
        if installer is None:
            raise RuntimeError("yum or dnf not found")

        with self.chroot_config() as dnf_config:
            installroot = path.absolute()
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
            ] + sorted(self.get_base_packages())

            # If eatmydata is available, we can use it to make boostrap significantly faster
            #
            # Disabled for now, this causes noise when dnf executes scriptlets
            # inside the target system
            #
            # eatmydata = shutil.which("eatmydata")
            # if eatmydata is not None:
            #     cmd.insert(0, eatmydata)
            images.host_run(cmd)

            # If dnf used a private rpmdb, promote it as the rpmdb of the newly
            # created system. See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1004863#32
            private_rpmdb = installroot / "root" / ".rpmdb"
            system_rpmdb = installroot / "var" / "lib" / "rpm"
            if os.path.isdir(private_rpmdb):
                images.logger.info("Moving %r to %r", private_rpmdb, system_rpmdb)
                if os.path.islink(system_rpmdb):
                    system_rpmdb = system_rpmdb.resolve()
                    if installroot.as_posix() not in os.path.commonprefix((system_rpmdb, installroot)):
                        raise RuntimeError(
                            f"/var/lib/rpm in installed system points to {system_rpmdb} which is outside installroot"
                        )
                shutil.rmtree(system_rpmdb)
                shutil.move(private_rpmdb, system_rpmdb)
            images.host_run(["chroot", installroot.as_posix(), "/usr/bin/rpmdb", "--rebuilddb"])


class YumDistro(RpmDistro):
    @override
    def get_base_packages(self) -> list[str]:
        return super().get_base_packages() + ["yum"]

    @override
    def get_update_pkgdb_script(self, script: Script) -> None:
        super().get_update_pkgdb_script(script)
        script.run(["/usr/bin/yum", "check-update", "-q", "-y"])

    @override
    def get_upgrade_system_script(self, script: Script) -> None:
        super().get_upgrade_system_script(script)
        script.run(["/usr/bin/yum", "upgrade", "-q", "-y"])

    @override
    def get_install_packages_script(self, script: Script, packages: list[str]) -> None:
        super().get_install_packages_script(script, packages)
        script.run(["/usr/bin/yum", "install", "-q", "-y"] + packages)


class DnfDistro(RpmDistro):
    @override
    def get_base_packages(self) -> list[str]:
        return super().get_base_packages() + ["dnf"]

    @override
    def get_setup_network_script(self, script: Script) -> None:
        super().get_setup_network_script(script)
        script.run(["/usr/bin/systemctl", "mask", "--now", "systemd-resolved"])

    @override
    def get_update_pkgdb_script(self, script: Script) -> None:
        super().get_update_pkgdb_script(script)
        script.run(["/usr/bin/dnf", "check-update", "-q", "-y"])

    @override
    def get_upgrade_system_script(self, script: Script) -> None:
        super().get_upgrade_system_script(script)
        script.run(["/usr/bin/dnf", "upgrade", "-q", "-y"])

    @override
    def get_install_packages_script(self, script: Script, packages: list[str]) -> None:
        super().get_install_packages_script(script, packages)
        script.run(["/usr/bin/dnf", "install", "-q", "-y"] + packages)

    @override
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

        with open("/tmp/script", "w") as fd:
            fd.write(script)
        res = subprocess.run(["/usr/bin/python3", "/tmp/script"], stdout=subprocess.PIPE, text=True, check=True)
        return cast(dict[str, dict[str, str]], json.loads(res.stdout))


class Centos7(YumDistro):
    mirror = "http://mirror.centos.org"
    baseurl = "{mirror}/centos/7/os/$basearch"

    def __init__(self, family: DistroFamily) -> None:
        super().__init__(family, "7", "7")

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("quay.io/centos/centos", "centos7")

    @override
    def bootstrap(self, images: Images, path: Path) -> None:
        super().bootstrap(images, path)
        installroot = path.absolute()
        varsdir = installroot / "etc" / "yum" / "vars"
        os.makedirs(varsdir, exist_ok=True)
        with open(varsdir / "releasever", "w") as fd:
            print("7", file=fd)


class Centos8(DnfDistro):
    mirror = "https://vault.centos.org"
    baseurl = "{mirror}/centos/8/BaseOS//$basearch/os/"

    def __init__(self, family: DistroFamily) -> None:
        super().__init__(family, "8", "8")

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("quay.io/centos/centos", "centos8")

    @override
    def bootstrap(self, images: Images, path: Path) -> None:
        super().bootstrap(images, path)
        # self.system.local_run(["tar", "-C", self.system.path, "-zxf", "images/centos8.tar.gz"])
        # Fixup repository information to point at the vault
        for p in (path / "etc/yum.repos.d/").glob("CentOS-*"):
            images.logger.info("Updating %s to point mirrors to the Vault", p)
            with p.open() as fd:
                st = os.stat(fd.fileno())
                with atomic_writer(p, mode="wt", chmod=stat.S_IMODE(st.st_mode)) as tf:
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
    version: str

    def __init__(self, family: DistroFamily, version: int):
        super().__init__(family, str(version), str(version))
        self.baseurl = f"{self.mirror}/pub/fedora/linux/releases/{version}/Everything/$basearch/os/"

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("registry.fedoraproject.org/fedora", self.name)

    @override
    def get_base_packages(self) -> list[str]:
        res = super().get_base_packages()
        if int(self.version) >= 42:
            res += ["systemd"]
        return res


class RockyDistro(DnfDistro):
    mirror = "http://dl.rockylinux.org"

    def __init__(self, family: DistroFamily, version: int) -> None:
        super().__init__(family, str(version), str(version))
        self.baseurl = f"{self.mirror}/pub/rocky/{version}/BaseOS/$basearch/os/"

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("quay.io/rockylinux/rockylinux", self.name)


class Fedora(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(FedoraDistro(self, 32))
        self.add_distro(FedoraDistro(self, 33))
        self.add_distro(FedoraDistro(self, 34))
        self.add_distro(FedoraDistro(self, 35))
        self.add_distro(FedoraDistro(self, 36))
        self.add_distro(FedoraDistro(self, 37))
        self.add_distro(FedoraDistro(self, 38))
        self.add_distro(FedoraDistro(self, 39))
        self.add_distro(FedoraDistro(self, 40))
        self.add_distro(FedoraDistro(self, 41))
        self.add_distro(FedoraDistro(self, 42))


class Rocky(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(RockyDistro(self, 8))
        self.add_distro(RockyDistro(self, 9))


class Centos(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(Centos7(self))
        self.add_distro(Centos8(self))
