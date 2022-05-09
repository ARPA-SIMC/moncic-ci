from __future__ import annotations
import re
import unittest

from moncic.distro import DistroFamily
from moncic.unittest import DistroTestMixin


class Fedora36(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = DistroFamily.lookup_distro("fedora36")

        with self.mock_system(distro) as system:
            system.bootstrap()
        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.path} --releasever=36 install bash rootfiles dbus dnf'))
        log.assertPopFirst('/usr/bin/rpmdb --rebuilddb')
        log.assertLogEmpty()

    def test_upgrade(self):
        distro = DistroFamily.lookup_distro("fedora36")

        with self.mock_system(distro) as system:
            system.update()
        log = system.run_log

        log.assertPopFirst('/usr/bin/dnf upgrade -q -y')
        log.assertPopFirst("cachedir_tag:")
        log.assertLogEmpty()
