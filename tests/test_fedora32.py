from __future__ import annotations
import re
import unittest

from moncic.distro import Distro
from moncic.unittest import DistroTestMixin


class Fedora32(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = Distro.create("fedora32")

        with self.mock_system(distro) as system:
            bootstrapper = system.create_bootstrapper()
            bootstrapper.bootstrap()
        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.root}')
        log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.root} --releasever=32 install bash vim-minimal dnf rootfiles git dbus'))
        log.assertLogEmpty()

    def test_upgrade(self):
        distro = Distro.create("fedora32")

        with self.mock_system(distro) as system:
            runner = system.create_maintenance_run()
            runner.update()
        log = system.run_log

        log.assertPopFirst('/usr/bin/rpmdb --rebuilddb')
        log.assertPopFirst("/usr/bin/sed -i '/^tsflags=/d' /etc/dnf/dnf.conf")
        log.assertPopFirst('/usr/bin/dnf install -y --allowerasing @buildsys-build')
        log.assertPopFirst("/usr/bin/dnf install -q -y 'dnf-command(builddep)'")
        log.assertPopFirst('/usr/bin/dnf install -q -y git')
        log.assertPopFirst('/usr/bin/dnf install -q -y rpmdevtools')
        log.assertPopFirst('/usr/bin/dnf copr enable -y simc/stable')
        log.assertPopFirst('/usr/bin/dnf upgrade -q -y')
        log.assertLogEmpty()
