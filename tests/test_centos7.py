from __future__ import annotations
import re
import unittest

from moncic.distro import DistroFamily
from moncic.unittest import DistroTestMixin


class Centos7(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = DistroFamily.lookup_distro("centos7")

        with self.mock() as run_log:
            with self.make_system(distro) as system:
                system.bootstrap()

        run_log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        run_log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.path} --releasever=7 install bash rootfiles dbus yum'))
        run_log.assertPopFirst('/usr/bin/rpmdb --rebuilddb')
        run_log.assertLogEmpty()

    def test_upgrade(self):
        distro = DistroFamily.lookup_distro("centos7")

        with self.mock() as run_log:
            with self.make_system(distro) as system:
                system.update()

        run_log.assertPopFirst('/usr/bin/yum upgrade -q -y')
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()
