from __future__ import annotations
import re
import unittest

from moncic.distro import Distro
from moncic.unittest import DistroTestMixin


class Centos7(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = Distro.create("centos7")

        with self.mock_system(distro) as system:
            run = system.create_maintenance_run()
            run.bootstrap()
        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(re.compile(
            rf"/usr/bin/dnf -c \S+\.repo -y '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
            rf' --installroot={system.path} --releasever=7 install bash vim-minimal yum rootfiles dbus'))
        log.assertLogEmpty()

    def test_upgrade(self):
        distro = Distro.create("centos7")

        with self.mock_system(distro) as system:
            with system.create_maintenance_run() as run:
                run.update()
        log = system.run_log

        log.assertPopFirst("/usr/bin/sed -i '/^tsflags=/d' /etc/yum.conf")
        log.assertPopFirst("/usr/bin/yum install -y epel-release")
        log.assertPopFirst("/usr/bin/yum install -y @buildsys-build")
        log.assertPopFirst("/usr/bin/yum install -y yum-utils")
        log.assertPopFirst("/usr/bin/yum install -y git")
        log.assertPopFirst("/usr/bin/yum install -y rpmdevtools")
        log.assertPopFirst("/usr/bin/yum install -q -y yum-plugin-copr")
        log.assertPopFirst('/usr/bin/yum copr enable -q -y simc/stable epel-7')
        log.assertPopFirst('/usr/bin/yum upgrade -q -y')
        log.assertLogEmpty()
