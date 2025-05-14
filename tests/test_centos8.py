from __future__ import annotations

import re
import unittest

from moncic.distro import DistroFamily
from moncic.unittest import DistroTestMixin


class Centos8(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = DistroFamily.lookup_distro("centos8")

        with self.mock() as run_log:
            with self.make_images(distro) as images:
                image = images.image("test")
                image.bootstrap()
                with image.system() as system:
                    path = system.path

        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path}\.new --releasever=8 install bash dbus rootfiles iproute dnf"
            )
        )
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()

    def test_upgrade(self):
        distro = DistroFamily.lookup_distro("centos8")

        with self.mock() as run_log:
            with self.make_system(distro) as system:
                system.update()

        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()
