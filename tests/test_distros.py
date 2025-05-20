from __future__ import annotations

import abc
import re
import unittest
from pathlib import Path
from typing import Any, ClassVar, override

from moncic.distro import Distro, DistroFamily
from moncic.unittest import DistroTestMixin, MockRunLog


class DistroTests(DistroTestMixin, unittest.TestCase, abc.ABC):
    NAME: ClassVar[str]
    distro: ClassVar[Distro]

    def __init_subclass__(cls, name: str, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.NAME = name

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.distro = DistroFamily.lookup_distro(cls.NAME)

    @abc.abstractmethod
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None: ...

    @abc.abstractmethod
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None: ...

    def test_bootstrap(self):
        with self.mock() as run_log:
            with self.make_images(self.distro) as images:
                image = images.image("test")
                image.bootstrap()

        self.assertBootstrapCommands(run_log, image.path)

    def test_update(self):
        with self.mock() as run_log:
            with self.make_images(self.distro) as images:
                image = images.image("test")
                image.update()

        self.assertUpdateCommands(run_log, image.path)


class TestBullseye(DistroTests, name="bullseye"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"(/usr/bin/eatmydata )?debootstrap --include=bash,dbus,systemd,apt-utils,eatmydata,iproute2"
                rf" --variant=minbase bullseye {path}.new http://deb.debian.org/debian"
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
            " satisfy bash dbus systemd apt-utils eatmydata iproute2"
        )
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestCentos7(DistroTests, name="centos7"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=7 install bash dbus rootfiles iproute yum"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/yum updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/yum upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/yum install -q -y bash dbus rootfiles iproute yum")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestCentos8(DistroTests, name="centos8"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=8 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestFedora32(DistroTests, name="fedora32"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=32 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestFedora34(DistroTests, name="fedora34"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=34 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestFedora36(DistroTests, name="fedora36"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=36 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestFedora38(DistroTests, name="fedora38"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=38 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestFedora40(DistroTests, name="fedora40"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=40 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestFedora42(DistroTests, name="fedora42"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=42 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestRocky8(DistroTests, name="rocky8"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=8 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


class TestRocky9(DistroTests, name="rocky9"):
    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=9 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()


del DistroTests
