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
            images.host_run(["systemd-nspawn", "-D", installroot.as_posix(), "/usr/bin/rpmdb", "--rebuilddb"])


class YumDistro(RpmDistro):
    @override
    def get_base_packages(self) -> list[str]:
        return super().get_base_packages() + ["yum"]

    @override
    def get_update_pkgdb_script(self, script: Script) -> None:
        super().get_update_pkgdb_script(script)
        script.run(["/usr/bin/yum", "check-update", "-q", "-y"], check=False)

    @override
    def get_upgrade_system_script(self, script: Script) -> None:
        super().get_upgrade_system_script(script)
        script.run(["/usr/bin/yum", "upgrade", "-q", "-y"])

    @override
    def get_install_packages_script(self, script: Script, packages: list[str]) -> None:
        super().get_install_packages_script(script, packages)
        script.run(["/usr/bin/yum", "install", "-q", "-y"] + packages)

    @override
    def get_prepare_build_script(self, script: Script) -> None:
        super().get_prepare_build_script(script)
        script.run(["/usr/bin/yum", "install", "-q", "-y", "@buildsys-build", "git", "rpmdevtools"])


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
        # check-update returns with code 100 if there are packages to upgrade
        script.run(["/usr/bin/dnf", "check-update", "-q", "-y"], check=False)

    @override
    def get_upgrade_system_script(self, script: Script) -> None:
        super().get_upgrade_system_script(script)
        script.run(["/usr/bin/dnf", "upgrade", "-q", "-y"])

    @override
    def get_install_packages_script(self, script: Script, packages: list[str]) -> None:
        super().get_install_packages_script(script, packages)
        script.run(["/usr/bin/dnf", "install", "-q", "-y"] + packages)

    @override
    def get_prepare_build_script(self, script: Script) -> None:
        super().get_prepare_build_script(script)
        script.run(["/usr/bin/dnf", "install", "-q", "-y", "dnf-command(builddep)", "git", "rpmdevtools"])

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
    mirror = "https://vault.centos.org"
    baseurl = "{mirror}/centos/7/os/$basearch"

    def __init__(self, family: DistroFamily) -> None:
        super().__init__(family, "7", "7", cgroup_v1=True)

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


class FedoraDistro(DnfDistro):
    version: str

    def __init__(self, family: DistroFamily, version: int, archived: bool = False):
        super().__init__(family, str(version), str(version))
        if archived:
            self.mirror = "https://archives.fedoraproject.org"
            self.baseurl = f"{self.mirror}/pub/archive/fedora/linux/releases/{version}/Everything/$basearch/os/"
        else:
            self.mirror = "https://download.fedoraproject.org"
            self.baseurl = f"{self.mirror}/pub/fedora/linux/releases/{version}/Everything/$basearch/os/"

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("registry.fedoraproject.org/fedora", self.name)

    @override
    def get_base_packages(self) -> list[str]:
        res = super().get_base_packages()
        if int(self.version) >= 41:
            res += ["systemd"]
        return res


class AlmaDistro(DnfDistro):
    mirror = "http://repo.almalinux.org"

    def __init__(self, family: DistroFamily, version: int) -> None:
        super().__init__(family, str(version), str(version))
        self.baseurl = f"{self.mirror}/almalinux/{version}/BaseOS/$basearch/os/"

    @override
    def get_podman_name(self) -> tuple[str, str]:
        return ("docker.io/library/almalinux", self.name)


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
        self.add_distro(FedoraDistro(self, 32, archived=True))
        self.add_distro(FedoraDistro(self, 33, archived=True))
        self.add_distro(FedoraDistro(self, 34, archived=True))
        self.add_distro(FedoraDistro(self, 35, archived=True))
        self.add_distro(FedoraDistro(self, 36, archived=True))
        self.add_distro(FedoraDistro(self, 37))
        self.add_distro(FedoraDistro(self, 38))
        self.add_distro(FedoraDistro(self, 39))
        self.add_distro(FedoraDistro(self, 40))
        self.add_distro(FedoraDistro(self, 41))
        self.add_distro(FedoraDistro(self, 42))


class Almalinux(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(AlmaDistro(self, 8))
        self.add_distro(AlmaDistro(self, 9))


class Rocky(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(RockyDistro(self, 8))
        self.add_distro(RockyDistro(self, 9))


class Centos(DistroFamily):
    @override
    def init(self) -> None:
        self.add_distro(Centos7(self))
