import unittest

from moncic.build.arpa import ARPA, RPM
from moncic.build.build import Build
from moncic.build.debian import Debian

COMMON_BUILD_PROFILES = ["artifacts_dir", "source_only", "on_success", "on_fail", "on_end"]


class TestBuild(unittest.TestCase):
    def test_build(self) -> None:
        self.assertEqual([x[0] for x in Build.build_config_class.list_build_options()], COMMON_BUILD_PROFILES)

    def test_debian(self) -> None:
        self.assertEqual(
            [x[0] for x in Debian.build_config_class.list_build_options()],
            COMMON_BUILD_PROFILES + ["build_profile", "include_source"],
        )

    def test_rpm(self) -> None:
        self.assertEqual([x[0] for x in RPM.build_config_class.list_build_options()], COMMON_BUILD_PROFILES)

    def test_arpa(self) -> None:
        self.assertEqual([x[0] for x in ARPA.build_config_class.list_build_options()], COMMON_BUILD_PROFILES)
