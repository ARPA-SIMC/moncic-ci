from __future__ import annotations
import re
import unittest

from moncic.distro import DistroFamily
from moncic.unittest import DistroTestMixin


class Centos7(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = DistroFamily.lookup_distro("centos7")

        with self.mock_system(distro) as system:
            system.bootstrap()
        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.path} --releasever=7 install bash rootfiles dbus yum'))
        log.assertLogEmpty()

    def test_upgrade(self):
        distro = DistroFamily.lookup_distro("centos7")

        with self.mock_system(distro) as system:
            system.update()
        log = system.run_log

        log.assertPopFirst('/usr/bin/yum upgrade -q -y')
        log.assertPopFirst("cachedir_tag:")
        log.assertLogEmpty()
