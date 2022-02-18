from __future__ import annotations
import re
import unittest

from moncic.distro import DistroFamily
from moncic.unittest import DistroTestMixin


class Rocky8(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = DistroFamily.lookup_distro("rocky8")

        with self.mock_system(distro) as system:
            system.bootstrap()
        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.path} --releasever=8 install bash rootfiles dbus dnf'))
        log.assertLogEmpty()

    def test_upgrade(self):
        distro = DistroFamily.lookup_distro("rocky8")

        with self.mock_system(distro) as system:
            system.update()
        log = system.run_log

        log.assertPopFirst('/usr/bin/dnf upgrade -q -y')
        log.assertLogEmpty()
