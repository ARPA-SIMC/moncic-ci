from __future__ import annotations

import re
import unittest

from moncic.distro import DistroFamily
from moncic.unittest import DistroTestMixin


class Bullseye(DistroTestMixin, unittest.TestCase):
    def test_bootstrap(self):
        distro = DistroFamily.lookup_distro("bullseye")

        with self.mock() as run_log:
            with self.make_images(distro) as images:
                images.bootstrap_system("test")
                with images.system("test") as system:
                    path = system.path

        run_log.assertPopFirstOptional(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(
            re.compile(
                rf"(/usr/bin/eatmydata )?debootstrap --include=bash,dbus,systemd,apt-utils,eatmydata,iproute2"
                rf" --variant=minbase bullseye {path}.new http://deb.debian.org/debian"
            )
        )
        run_log.assertLogEmpty()

    def test_upgrade(self):
        distro = DistroFamily.lookup_distro("bullseye")

        with self.mock() as run_log:
            with self.make_system(distro) as system:
                system.update()

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
