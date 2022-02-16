from __future__ import annotations
import re
import unittest

from moncic.distro import Distro
from moncic.unittest import DistroTestMixin


class Fedora34(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = Distro.create("fedora34")

        with self.mock_system(distro) as system:
            run = system.create_maintenance_run()
            run.bootstrap()
        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.path} --releasever=34 install bash vim-minimal dnf rootfiles git dbus'))
        log.assertLogEmpty()

    def test_upgrade(self):
        distro = Distro.create("fedora34")

        with self.mock_system(distro) as system:
            with system.create_maintenance_run() as run:
                run.update()
        log = system.run_log

        log.assertPopFirst("/usr/bin/sed -i '/^tsflags=/d' /etc/dnf/dnf.conf")
        log.assertPopFirst('/usr/bin/dnf install -y --allowerasing @buildsys-build')
        log.assertPopFirst("/usr/bin/dnf install -q -y 'dnf-command(builddep)'")
        log.assertPopFirst('/usr/bin/dnf install -q -y git')
        log.assertPopFirst('/usr/bin/dnf install -q -y rpmdevtools')
        log.assertPopFirst('/usr/bin/dnf copr enable -y simc/stable')
        log.assertPopFirst('/usr/bin/dnf upgrade -q -y')
        log.assertLogEmpty()
