from __future__ import annotations

import unittest

from moncic.build.build import Build
from moncic.build.debian import Debian
from moncic.build.arpa import RPM, ARPA


class TestBuild(unittest.TestCase):
    def test_build(self):
        self.assertEqual(
            [x[0] for x in Build.list_build_options()],
            [])

    def test_debian(self):
        self.assertEqual(
            [x[0] for x in Debian.list_build_options()],
            ["build_profile"])

    def test_rpm(self):
        self.assertEqual(
            [x[0] for x in RPM.list_build_options()],
            [])

    def test_arpa(self):
        self.assertEqual(
            [x[0] for x in ARPA.list_build_options()],
            [])
