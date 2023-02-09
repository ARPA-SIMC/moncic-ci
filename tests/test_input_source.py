from __future__ import annotations

import os
import tempfile
import unittest

from moncic.exceptions import Fail
from moncic.source import debian, source

from .source import GitRepo, MockBuilder


class TestInputSource(unittest.TestCase):
    def test_random_file(self):
        with tempfile.TemporaryDirectory() as workdir:
            random_file = os.path.join(workdir, "testfile")
            with open(random_file, "wt"):
                pass

            isrc = source.InputSource.create(random_file)
            self.assertIsInstance(isrc, source.LocalFile)
            self.assertEqual(isrc.source, random_file)
            self.assertEqual(isrc.path, random_file)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("sid"))

            isrc = source.InputSource.create("file:" + random_file)
            self.assertIsInstance(isrc, source.LocalFile)
            self.assertEqual(isrc.source, "file:" + random_file)
            self.assertEqual(isrc.path, random_file)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("sid"))

    def test_debian_dsc(self):
        with tempfile.TemporaryDirectory() as workdir:
            dsc_file = os.path.join(workdir, "testfile.dsc")
            with open(dsc_file, "wt"):
                pass

            isrc = source.InputSource.create(dsc_file)
            self.assertIsInstance(isrc, source.LocalFile)
            self.assertEqual(isrc.source, dsc_file)
            self.assertEqual(isrc.path, dsc_file)
            with self.assertRaises(NotImplementedError):
                src = isrc.detect_source(MockBuilder("sid"))
                self.assertIsInstance(src, debian.DebianSourcePackage)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            isrc = source.InputSource.create("file:" + dsc_file)
            self.assertIsInstance(isrc, source.LocalFile)
            self.assertEqual(isrc.source, "file:" + dsc_file)
            self.assertEqual(isrc.path, dsc_file)
            with self.assertRaises(NotImplementedError):
                src = isrc.detect_source(MockBuilder("sid"))
                self.assertIsInstance(src, debian.DebianSourcePackage)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

    def test_random_dir(self):
        with tempfile.TemporaryDirectory() as workdir:
            isrc = source.InputSource.create(workdir)
            self.assertIsInstance(isrc, source.LocalDir)
            self.assertEqual(isrc.source, workdir)
            self.assertEqual(isrc.path, workdir)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("sid"))

            isrc = source.InputSource.create("file:" + workdir)
            self.assertIsInstance(isrc, source.LocalDir)
            self.assertEqual(isrc.source, "file:" + workdir)
            self.assertEqual(isrc.path, workdir)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("sid"))

    def test_debian_dir(self):
        with tempfile.TemporaryDirectory() as workdir:
            os.mkdir(os.path.join(workdir, "debian"))

            isrc = source.InputSource.create(workdir)
            self.assertIsInstance(isrc, source.LocalDir)
            self.assertEqual(isrc.source, workdir)
            self.assertEqual(isrc.path, workdir)
            src = isrc.detect_source(MockBuilder("sid"))
            self.assertIsInstance(src, debian.DebianSourceDir)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            isrc = source.InputSource.create("file:" + workdir)
            self.assertIsInstance(isrc, source.LocalDir)
            self.assertEqual(isrc.source, "file:" + workdir)
            self.assertEqual(isrc.path, workdir)
            src = isrc.detect_source(MockBuilder("sid"))
            self.assertIsInstance(src, debian.DebianSourceDir)
            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

    def test_git_dir(self):
        with GitRepo() as git:
            git.add("testfile")
            git.commit()
            isrc = source.InputSource.create(git.root)
            self.assertIsInstance(isrc, source.LocalGit)
            self.assertEqual(isrc.source, git.root)
            self.assertEqual(isrc.repo.working_dir, git.root)
            self.assertFalse(isrc.copy)
            self.assertEqual(isrc.orig_path, git.root)

            isrc = source.InputSource.create("file:" + git.root)
            self.assertIsInstance(isrc, source.LocalGit)
            self.assertEqual(isrc.source, "file:" + git.root)
            self.assertEqual(isrc.repo.working_dir, git.root)
            self.assertFalse(isrc.copy)
            self.assertEqual(isrc.orig_path, git.root)
            # src = isrc.detect_source(MockBuilder("sid"))
            # self.assertIsInstance(src, debian.DebianSourceDir)

            with MockBuilder("sid") as builder:
                clone = isrc.clone(builder)
                self.assertIsInstance(clone, source.LocalGit)
                self.assertNotEqual(clone.repo.working_dir, git.root)
                self.assertTrue(clone.copy)
                self.assertEqual(isrc.orig_path, git.root)

    def test_url(self):
        url = "http://localhost/test"

        isrc = source.InputSource.create(url)
        self.assertIsInstance(isrc, source.URL)
        self.assertEqual(isrc.source, url)
        self.assertEqual(isrc.parsed.scheme, "http")
        self.assertEqual(isrc.parsed.path, "/test")
        # with self.assertRaises(Fail):
        #     isrc.detect_source(MockBuilder("sid"))
