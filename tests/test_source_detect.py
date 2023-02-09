from __future__ import annotations

import unittest

from moncic.exceptions import Fail
from moncic.source import debian, rpm, source

from .source import GitRepo, MockBuilder


class GitFixtureMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git = GitRepo()
        cls.git.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.git.__exit__(None, None, None)
        super().tearDownClass()


class TestDetectDebianPlainGit(GitFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.commit()

    def test_detect_local(self):
        isrc = source.InputSource.create(self.git.root)
        self.assertIsInstance(isrc, source.LocalGit)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianPlainGit)

    def test_detect_url(self):
        with self.git.serve() as url:
            isrc = source.InputSource.create(url)
            self.assertIsInstance(isrc, source.URL)

            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertIsInstance(src, debian.DebianPlainGit)


class DetectDebianGBPTestUpstreamMixin(GitFixtureMixin):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")

        # Debian branch
        cls.git.git("checkout", "-b", cls.packaging_branch_name)
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("testfile", "test content")
        cls.git.commit("Updated testfile")

    def test_detect_local(self):
        isrc = source.InputSource.create(self.git.root)
        self.assertIsInstance(isrc, source.LocalGit)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianGBPTestUpstream)

    def test_detect_url(self):
        with self.git.serve() as url:
            isrc = source.InputSource.create(url)
            self.assertIsInstance(isrc, source.URL)

            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertIsInstance(src, debian.DebianGBPTestUpstream)


class TestDetectDebianGBPTestUpstreamUnstable(DetectDebianGBPTestUpstreamMixin, unittest.TestCase):
    packaging_branch_name = "debian/unstable"


class TestDetectDebianGBPTestUpstreamSid(DetectDebianGBPTestUpstreamMixin, unittest.TestCase):
    packaging_branch_name = "debian/sid"


# class DebianGBPRelease(DebianGBP):
# class DebianGBPTestDebian(DebianGBP):
# class DebianSourceDir(DebianSource):
# class DebianSourcePackage(DebianSource):
# class ARPAGit(RPMGit):
