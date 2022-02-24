from __future__ import annotations
import os
import tempfile
import unittest

from moncic.unittest import DistroTestMixin, MockSystem
from moncic.system import Config
from moncic.container import UserConfig
from moncic.moncic import Moncic


class Bootstrap(DistroTestMixin, unittest.TestCase):
    def test_tarball(self):
        with tempfile.TemporaryDirectory() as imagedir:
            # Create a mock tarball for fedora34
            tar_path = os.path.join(imagedir, "fedora34.tar.gz")
            with open(tar_path, "wb"):
                pass

            with open(os.path.join(imagedir, "test.yaml"), "wt") as fd:
                print("distro: fedora34", file=fd)

            config = Config.load(os.path.join(imagedir, "test"))
            system = MockSystem(Moncic(imagedir=imagedir), config)
            system.attach_testcase(self)

            system.bootstrap()

        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(f"tar -C {system.path} -zxf {tar_path}")
        log.assertLogEmpty()

    def test_forward_user(self):
        user = UserConfig.from_sudoer()

        with tempfile.TemporaryDirectory() as imagedir:
            with open(os.path.join(imagedir, "test.yaml"), "wt") as fd:
                print("distro: fedora34", file=fd)
                print(f"forward_user: {user.user_name}", file=fd)

            config = Config.load(os.path.join(imagedir, "test"))
            system = MockSystem(Moncic(imagedir=imagedir), config)
            system.attach_testcase(self)
            system.update()

        log = system.run_log

        log.assertPopFirst(f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}")
        log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        log.assertLogEmpty()
