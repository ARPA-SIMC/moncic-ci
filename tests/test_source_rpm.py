from __future__ import annotations

import os
import unittest

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import InputSource, debian, inputsource, rpm
from moncic.unittest import make_moncic

from .source import GitFixtureMixin, MockBuilder, WorkdirFixtureMixin

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestARPA(GitFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        travis_yml = os.path.join(cls.workdir, ".travis.yml")
        with open(travis_yml, "wt") as out:
            print("foo foo simc/stable bar bar", file=out)

        # Initial upstream
        cls.git.add(
            ".travis.yml",
            """
foo foo simc/stable bar bar
""",
        )
        cls.git.add("fedora/SPECS/test.spec")
        cls.git.commit()

    def test_detect_local(self):
        with InputSource.create(self.git.root) as isrc:
            self.assertIsInstance(isrc, inputsource.LocalGit)

            with self.assertRaises(Fail):
                isrc.detect_source(SID)

            src = isrc.detect_source(ROCKY9)
            self.assertIsInstance(src, rpm.ARPAGitSource)

    def test_detect_url(self):
        with self.git.serve() as url:
            with InputSource.create(url) as isrc:
                self.assertIsInstance(isrc, inputsource.URL)

                with self.assertRaises(Fail):
                    isrc.detect_source(SID)

                src = isrc.detect_source(ROCKY9)
                self.assertIsInstance(src, rpm.ARPAGitSource)

    def _test_build_source(self, path):
        with InputSource.create(path) as isrc:
            src = isrc.detect_source(ROCKY9)
            self.assertEqual(src.get_build_class().__name__, "ARPA")
            build = src.make_build(distro=ROCKY9)
            self.assertTrue(build.source.host_path.is_dir())
            with (
                make_moncic() as moncic,
                moncic.session(),
                MockBuilder("rocky9", build) as builder,
                builder.container() as container,
            ):
                src.gather_sources_from_host(builder.build, container)
                self.assertCountEqual(os.listdir(container.source_dir), [])
                # TODO: @guest_only
                # TODO: def build_source_package(self) -> str:

    def test_build_source_git(self):
        self._test_build_source(self.git.root)

    def test_build_source_url(self):
        with self.git.serve() as url:
            self._test_build_source(url)
