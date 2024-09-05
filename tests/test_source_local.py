from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import File, Dir, Git
from moncic.source.debian import DebianDsc

from .source import WorkdirFixture, GitFixture

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestSourceFile(WorkdirFixture):
    file: Path
    dsc: Path

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.file = cls.workdir / "testfile"
        cls.file.touch()
        cls.dsc = cls.workdir / "testfile.dsc"
        cls.dsc.touch()

    def test_plain_file_from_path(self):
        with Source.create(source=self.file) as src:
            self.assertIsInstance(src, File)
            self.assertEqual(src.name, self.file.as_posix())
            self.assertEqual(src.path, self.file)

    def test_plain_file_from_url(self):
        with Source.create(source=f"file:{self.file}") as src:
            self.assertIsInstance(src, File)
            self.assertEqual(src.name, f"file:{self.file}")
            self.assertEqual(src.path, self.file)

    def test_fail_if_branch_used(self):
        with self.assertRaisesRegexp(Fail, "Cannot specify a branch when working on a file"):
            Source.create(source=self.file, branch="test")

    def test_plain_file_make_buildable(self):
        with Source.create(source=self.file) as src:
            with self.assertRaisesRegexp(Fail, f"{self.file}: cannot detect source type"):
                src.make_buildable(distro=ROCKY9)
            with self.assertRaisesRegexp(Fail, f"{self.file}: cannot detect source type"):
                src.make_buildable(distro=SID)

    def test_dsc_file_make_buildable(self):
        with Source.create(source=self.dsc) as src:
            with self.assertRaisesRegexp(Fail, f"{self.dsc}: cannot build Debian source package on rocky:9"):
                src.make_buildable(distro=ROCKY9)

            newsrc = src.make_buildable(distro=SID)
            self.assertIsInstance(newsrc, DebianDsc)


# class TestInputSource(unittest.TestCase):
#     def test_random_dir(self):
#         with tempfile.TemporaryDirectory() as workdir:
#             with InputSource.create(workdir) as isrc:
#                 self.assertIsInstance(isrc, inputsource.LocalDir)
#                 self.assertEqual(isrc.source, workdir)
#                 self.assertEqual(isrc.path, workdir)
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(SID)
#
#             with InputSource.create("file:" + workdir) as isrc:
#                 self.assertIsInstance(isrc, inputsource.LocalDir)
#                 self.assertEqual(isrc.source, "file:" + workdir)
#                 self.assertEqual(isrc.path, workdir)
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(SID)
#
#     def test_debian_dir(self):
#         with tempfile.TemporaryDirectory() as workdir:
#             os.mkdir(os.path.join(workdir, "debian"))
#
#             with InputSource.create(workdir) as isrc:
#                 self.assertIsInstance(isrc, inputsource.LocalDir)
#                 self.assertEqual(isrc.source, workdir)
#                 self.assertEqual(isrc.path, workdir)
#                 src = isrc.detect_source(SID)
#                 self.assertIsInstance(src, debian.DebianSourceDir)
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(ROCKY9)
#
#             with InputSource.create("file:" + workdir) as isrc:
#                 self.assertIsInstance(isrc, inputsource.LocalDir)
#                 self.assertEqual(isrc.source, "file:" + workdir)
#                 self.assertEqual(isrc.path, workdir)
#                 src = isrc.detect_source(SID)
#                 self.assertIsInstance(src, debian.DebianSourceDir)
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(ROCKY9)
#
#
# class TestLocalGit(GitFixtureMixin, unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         cls.git.add("testfile")
#         cls.git.commit("Initial")
#
#         # Debian branch
#         cls.git.git("checkout", "-b", "branch1")
#         cls.git.add("test-branch1")
#         cls.git.commit()
#
#         # New changes to upstream branch
#         cls.git.git("checkout", "main")
#         cls.git.add("test-main")
#         cls.git.commit()
#
#     def test_create_path(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#             self.assertEqual(isrc.source, self.git.root.as_posix())
#             self.assertEqual(isrc.repo.working_dir, self.git.root.as_posix())
#             self.assertFalse(isrc.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#
#     def test_create_file_url(self):
#         with InputSource.create("file:" + self.git.root.as_posix()) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#             self.assertEqual(isrc.source, "file:" + self.git.root.as_posix())
#             self.assertEqual(isrc.repo.working_dir, self.git.root.as_posix())
#             self.assertFalse(isrc.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#
#     def test_clone(self):
#         with InputSource.create(self.git.root) as isrc:
#             clone = isrc.clone()
#             self.assertIsInstance(clone, inputsource.LocalGit)
#             self.assertNotEqual(clone.repo.working_dir, self.git.root)
#             self.assertTrue(clone.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#             self.assertEqual(isrc.repo.active_branch.name, "main")
#             self.assertEqual(clone.repo.active_branch.name, "main")
#             self.assertTrue(os.path.exists(clone.repo.working_dir))
#
#         self.assertTrue(os.path.exists(isrc.repo.working_dir))
#         self.assertFalse(os.path.exists(clone.repo.working_dir))
#
#     def test_clone_branch(self):
#         with InputSource.create(self.git.root) as isrc:
#             clone = isrc.clone("branch1").branch("branch1")
#             self.assertIsInstance(clone, inputsource.LocalGit)
#             self.assertNotEqual(clone.repo.working_dir, self.git.root)
#             self.assertTrue(clone.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#             self.assertEqual(isrc.repo.active_branch.name, "main")
#             self.assertEqual(clone.repo.active_branch.name, "branch1")
#             self.assertTrue(os.path.exists(clone.repo.working_dir))
#
#         self.assertTrue(os.path.exists(isrc.repo.working_dir))
#         self.assertFalse(os.path.exists(clone.repo.working_dir))
#
#     def test_branch(self):
#         with InputSource.create(self.git.root) as isrc:
#             clone = isrc.branch("branch1")
#             self.assertIsInstance(clone, inputsource.LocalGit)
#             self.assertNotEqual(clone.repo.working_dir, self.git.root)
#             self.assertTrue(clone.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#             self.assertEqual(isrc.repo.active_branch.name, "main")
#             self.assertEqual(clone.repo.active_branch.name, "branch1")
#             self.assertTrue(os.path.exists(clone.repo.working_dir))
#
#         self.assertTrue(os.path.exists(isrc.repo.working_dir))
#         self.assertFalse(os.path.exists(clone.repo.working_dir))
