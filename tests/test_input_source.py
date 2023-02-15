from __future__ import annotations

import os
import tempfile
import unittest

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import debian, source

from .source import GitRepo

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestInputSource(unittest.TestCase):
    def test_random_file(self):
        with tempfile.TemporaryDirectory() as workdir:
            random_file = os.path.join(workdir, "testfile")
            with open(random_file, "wt"):
                pass

            with source.InputSource.create(random_file) as isrc:
                self.assertIsInstance(isrc, source.LocalFile)
                self.assertEqual(isrc.source, random_file)
                self.assertEqual(isrc.path, random_file)
                with self.assertRaises(Fail):
                    isrc.detect_source(SID)

            with source.InputSource.create("file:" + random_file) as isrc:
                self.assertIsInstance(isrc, source.LocalFile)
                self.assertEqual(isrc.source, "file:" + random_file)
                self.assertEqual(isrc.path, random_file)
                with self.assertRaises(Fail):
                    isrc.detect_source(SID)

    def test_debian_dsc(self):
        with tempfile.TemporaryDirectory() as workdir:
            dsc_file = os.path.join(workdir, "testfile.dsc")
            with open(dsc_file, "wt"):
                pass

            with source.InputSource.create(dsc_file) as isrc:
                self.assertIsInstance(isrc, source.LocalFile)
                self.assertEqual(isrc.source, dsc_file)
                self.assertEqual(isrc.path, dsc_file)
                src = isrc.detect_source(SID)
                self.assertIsInstance(src, debian.DebianDsc)
                with self.assertRaises(Fail):
                    isrc.detect_source(ROCKY9)

            with source.InputSource.create("file:" + dsc_file) as isrc:
                self.assertIsInstance(isrc, source.LocalFile)
                self.assertEqual(isrc.source, "file:" + dsc_file)
                self.assertEqual(isrc.path, dsc_file)
                src = isrc.detect_source(SID)
                self.assertIsInstance(src, debian.DebianDsc)
                with self.assertRaises(Fail):
                    isrc.detect_source(ROCKY9)

    def test_random_dir(self):
        with tempfile.TemporaryDirectory() as workdir:
            with source.InputSource.create(workdir) as isrc:
                self.assertIsInstance(isrc, source.LocalDir)
                self.assertEqual(isrc.source, workdir)
                self.assertEqual(isrc.path, workdir)
                with self.assertRaises(Fail):
                    isrc.detect_source(SID)

            with source.InputSource.create("file:" + workdir) as isrc:
                self.assertIsInstance(isrc, source.LocalDir)
                self.assertEqual(isrc.source, "file:" + workdir)
                self.assertEqual(isrc.path, workdir)
                with self.assertRaises(Fail):
                    isrc.detect_source(SID)

    def test_debian_dir(self):
        with tempfile.TemporaryDirectory() as workdir:
            os.mkdir(os.path.join(workdir, "debian"))

            with source.InputSource.create(workdir) as isrc:
                self.assertIsInstance(isrc, source.LocalDir)
                self.assertEqual(isrc.source, workdir)
                self.assertEqual(isrc.path, workdir)
                src = isrc.detect_source(SID)
                self.assertIsInstance(src, debian.DebianSourceDir)
                with self.assertRaises(Fail):
                    isrc.detect_source(ROCKY9)

            with source.InputSource.create("file:" + workdir) as isrc:
                self.assertIsInstance(isrc, source.LocalDir)
                self.assertEqual(isrc.source, "file:" + workdir)
                self.assertEqual(isrc.path, workdir)
                src = isrc.detect_source(SID)
                self.assertIsInstance(src, debian.DebianSourceDir)
                with self.assertRaises(Fail):
                    isrc.detect_source(ROCKY9)

    def test_git_dir(self):
        with GitRepo() as git:
            git.add("testfile")
            git.commit()
            with source.InputSource.create(git.root) as isrc:
                self.assertIsInstance(isrc, source.LocalGit)
                self.assertEqual(isrc.source, git.root)
                self.assertEqual(isrc.repo.working_dir, git.root)
                self.assertFalse(isrc.copy)
                self.assertEqual(isrc.orig_path, git.root)

            with source.InputSource.create("file:" + git.root) as isrc:
                self.assertIsInstance(isrc, source.LocalGit)
                self.assertEqual(isrc.source, "file:" + git.root)
                self.assertEqual(isrc.repo.working_dir, git.root)
                self.assertFalse(isrc.copy)
                self.assertEqual(isrc.orig_path, git.root)
                # src = isrc.detect_source(MockBuilder("sid"))
                # self.assertIsInstance(src, debian.DebianSourceDir)

                clone = isrc.clone()
                self.assertIsInstance(clone, source.LocalGit)
                self.assertNotEqual(clone.repo.working_dir, git.root)
                self.assertTrue(clone.copy)
                self.assertEqual(isrc.orig_path, git.root)

            self.assertTrue(os.path.exists(isrc.repo.working_dir))
            self.assertFalse(os.path.exists(clone.repo.working_dir))

    def test_url(self):
        url = "http://localhost/test"

        with source.InputSource.create(url) as isrc:
            self.assertIsInstance(isrc, source.URL)
            self.assertEqual(isrc.source, url)
            self.assertEqual(isrc.parsed.scheme, "http")
            self.assertEqual(isrc.parsed.path, "/test")
            # with self.assertRaises(Fail):
            #     isrc.detect_source(MockBuilder("sid"))
