from __future__ import annotations

import os
import tempfile
import unittest

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import InputSource, debian, inputsource

from .source import GitFixtureMixin

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestURL(GitFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git.add("testfile")
        cls.git.commit("Initial")

        # Debian branch
        cls.git.git("checkout", "-b", "branch1")
        cls.git.add("test-branch1")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("test-main")
        cls.git.commit()

    def test_url(self):
        with self.git.serve() as url:
            with InputSource.create(url) as isrc:
                self.assertIsInstance(isrc, inputsource.URL)
                self.assertEqual(isrc.source, url)
                self.assertEqual(isrc.parsed.scheme, "http")
                self.assertEqual(isrc.parsed.path, "/.git")

                clone = isrc.clone()
                self.assertIsInstance(clone, inputsource.LocalGit)
                self.assertEqual(clone.repo.active_branch.name, "main")
                self.assertIsNone(clone.orig_path)

                clone = isrc.clone("branch1")
                self.assertIsInstance(clone, inputsource.LocalGit)
                self.assertEqual(clone.repo.active_branch.name, "branch1")
                self.assertIsNone(clone.orig_path)
