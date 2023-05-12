from __future__ import annotations

import os
import tempfile
import unittest

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import debian, source

from .source import GitFixtureMixin

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


class TestLocalGit(GitFixtureMixin, unittest.TestCase):
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

    def test_create_path(self):
        with source.InputSource.create(self.git.root) as isrc:
            self.assertIsInstance(isrc, source.LocalGit)
            self.assertEqual(isrc.source, self.git.root)
            self.assertEqual(isrc.repo.working_dir, self.git.root)
            self.assertFalse(isrc.copy)
            self.assertEqual(isrc.orig_path, self.git.root)

    def test_create_file_url(self):
        with source.InputSource.create("file:" + self.git.root) as isrc:
            self.assertIsInstance(isrc, source.LocalGit)
            self.assertEqual(isrc.source, "file:" + self.git.root)
            self.assertEqual(isrc.repo.working_dir, self.git.root)
            self.assertFalse(isrc.copy)
            self.assertEqual(isrc.orig_path, self.git.root)

    def test_clone(self):
        with source.InputSource.create(self.git.root) as isrc:
            clone = isrc.clone()
            self.assertIsInstance(clone, source.LocalGit)
            self.assertNotEqual(clone.repo.working_dir, self.git.root)
            self.assertTrue(clone.copy)
            self.assertEqual(isrc.orig_path, self.git.root)
            self.assertEqual(isrc.repo.active_branch.name, "main")
            self.assertEqual(clone.repo.active_branch.name, "main")
            self.assertTrue(os.path.exists(clone.repo.working_dir))

        self.assertTrue(os.path.exists(isrc.repo.working_dir))
        self.assertFalse(os.path.exists(clone.repo.working_dir))

    def test_clone_branch(self):
        with source.InputSource.create(self.git.root) as isrc:
            clone = isrc.clone("branch1").branch("branch1")
            self.assertIsInstance(clone, source.LocalGit)
            self.assertNotEqual(clone.repo.working_dir, self.git.root)
            self.assertTrue(clone.copy)
            self.assertEqual(isrc.orig_path, self.git.root)
            self.assertEqual(isrc.repo.active_branch.name, "main")
            self.assertEqual(clone.repo.active_branch.name, "branch1")
            self.assertTrue(os.path.exists(clone.repo.working_dir))

        self.assertTrue(os.path.exists(isrc.repo.working_dir))
        self.assertFalse(os.path.exists(clone.repo.working_dir))

    def test_branch(self):
        with source.InputSource.create(self.git.root) as isrc:
            clone = isrc.branch("branch1")
            self.assertIsInstance(clone, source.LocalGit)
            self.assertNotEqual(clone.repo.working_dir, self.git.root)
            self.assertTrue(clone.copy)
            self.assertEqual(isrc.orig_path, self.git.root)
            self.assertEqual(isrc.repo.active_branch.name, "main")
            self.assertEqual(clone.repo.active_branch.name, "branch1")
            self.assertTrue(os.path.exists(clone.repo.working_dir))

        self.assertTrue(os.path.exists(isrc.repo.working_dir))
        self.assertFalse(os.path.exists(clone.repo.working_dir))


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
            with source.InputSource.create(url) as isrc:
                self.assertIsInstance(isrc, source.URL)
                self.assertEqual(isrc.source, url)
                self.assertEqual(isrc.parsed.scheme, "http")
                self.assertEqual(isrc.parsed.path, "/.git")

                clone = isrc.clone()
                self.assertIsInstance(clone, source.LocalGit)
                self.assertEqual(clone.repo.active_branch.name, "main")
                self.assertIsNone(clone.orig_path)

                clone = isrc.clone("branch1")
                self.assertIsInstance(clone, source.LocalGit)
                self.assertEqual(clone.repo.active_branch.name, "branch1")
                self.assertIsNone(clone.orig_path)
