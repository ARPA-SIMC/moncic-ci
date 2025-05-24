from __future__ import annotations

import abc
import re
import unittest
from pathlib import Path
from typing import Any, ClassVar, override

from moncic.distro import Distro, DistroFamily
from moncic.provision.image import DistroImage
from moncic.unittest import MockRunLog, MoncicTestCase


class DistroTests(MoncicTestCase, unittest.TestCase, abc.ABC):
    NAME: ClassVar[str]
    distro: ClassVar[Distro]

    def __init_subclass__(cls, name: str, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.NAME = name
        cls.distro = DistroFamily.lookup_distro(cls.NAME)

    def setUp(self) -> None:
        super().setUp()
        mconfig = self.config()
        self.session = self.enterContext(self.mock_session(self.moncic(mconfig)))
        self.distro_image = DistroImage(session=self.session, name=self.NAME, distro=self.distro)
        self.image = self.session.images.image(self.NAME)

    @abc.abstractmethod
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None: ...

    @abc.abstractmethod
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None: ...

    def test_bootstrap(self):
        path = self.tempdir()
        self.distro.bootstrap(self.session.bootstrapper, path)
        self.assertBootstrapCommands(self.session.run_log, path)

    def test_update(self):
        self.image.update()
        self.session.run_log.assertPopFirst(f"{self.NAME}: container start")
        self.assertUpdateCommands(self.session.run_log, Path("/test"))
        self.session.run_log.assertPopFirst(f"{self.NAME}: container stop")
        self.session.run_log.assertLogEmpty()


class TestBullseye(DistroTests, name="bullseye"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"(/usr/bin/eatmydata )?debootstrap --include=bash,dbus,systemd,apt-utils,eatmydata,iproute2"
                rf" --variant=minbase bullseye {path} http://deb.debian.org/debian"
            )
        )
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/apt-get update")
        run_log.assertPopFirst(
            "/usr/bin/apt-get --assume-yes --quiet --show-upgraded '-o Dpkg::Options::=\"--force-confnew\"'"
            " full-upgrade"
        )
        run_log.assertPopFirst(
            "/usr/bin/apt-get --assume-yes --quiet --show-upgraded '-o Dpkg::Options::=\"--force-confnew\"'"
            " satisfy apt-utils bash dbus eatmydata iproute2 systemd"
        )


class TestCentos7(DistroTests, name="centos7"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=7 install bash dbus rootfiles iproute yum"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/yum updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/yum upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/yum install -q -y bash dbus iproute rootfiles yum")


class TestCentos8(DistroTests, name="centos8"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=8 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestFedora32(DistroTests, name="fedora32"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=32 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestFedora34(DistroTests, name="fedora34"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=34 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestFedora36(DistroTests, name="fedora36"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=36 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestFedora38(DistroTests, name="fedora38"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=38 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestFedora40(DistroTests, name="fedora40"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=40 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestFedora42(DistroTests, name="fedora42"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=42 install bash dbus rootfiles iproute dnf systemd"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles systemd")


class TestRocky8(DistroTests, name="rocky8"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=8 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


class TestRocky9(DistroTests, name="rocky9"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=9 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst(f"chroot {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus dnf iproute rootfiles")


del DistroTests
