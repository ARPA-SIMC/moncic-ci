from __future__ import annotations
import os
import tempfile
import unittest

from moncic.unittest import DistroTestMixin, MockSystem
from moncic.system import Config


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
            system = MockSystem(config)
            system.attach_testcase(self)

            bootstrapper = system.create_bootstrapper()
            bootstrapper.bootstrap()

        log = system.run_log

        log.assertPopFirst(f'btrfs -q subvolume create {system.path}')
        log.assertPopFirst(f"tar -C {system.path} -zxf {tar_path}")
        log.assertLogEmpty()
