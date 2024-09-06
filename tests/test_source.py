from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.source.source import CommandLog, SourceStack, Source
from moncic.source.local import Git

import git

from .source import GitFixture

if TYPE_CHECKING:
    from moncic.distro import Distro


class TestCommandLog(unittest.TestCase):
    def test_append(self) -> None:
        log = CommandLog()
        log.add_command("touch", "a b c")
        self.assertEqual(log, ["touch 'a b c'"])


class TestSourceStack(unittest.TestCase):
    def test_enter_once(self) -> None:
        with SourceStack() as stack:
            pass

    def test_enter_twice(self) -> None:
        with SourceStack() as stack:
            with self.assertRaisesRegexp(RuntimeError, "__enter__ called in multiple Sources of the same chain"):
                with stack:
                    pass


class MockSource(Source):
    def make_buildable(self, *, distro: Distro, source_type: str | None = None) -> Source:
        return self


class TestSource(GitFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git.add("test", b"test")
        cls.git.commit("initial commit")

        cls.git.add("test1", b"test1")
        cls.git.commit("second commit")
        cls.git.git("tag", "1.0")

        cls.git.git("checkout", "-b", "devel")
        cls.git.add("test2", b"test2")
        cls.git.commit("devel commit")

        cls.git.git("checkout", "main")

    def test_create(self) -> None:
        source = MockSource(name="test")
        self.assertIsNone(source.parent)
        self.assertIsNotNone(source.stack)
        self.assertIsNotNone(source.command_log)
        self.assertEqual(MockSource.get_source_type(), "mocksource")
        self.assertEqual(source.name, "test")
        self.assertEqual(str(source), "test")
        self.assertEqual(repr(source), "MockSource(test)")

    def test_derive(self) -> None:
        parent = MockSource(name="parent")
        child = MockSource(parent=parent)

        self.assertIsNone(parent.parent)
        self.assertIs(child.parent, parent)
        self.assertIs(parent.stack, child.stack)
        self.assertIsNot(parent.command_log, child.command_log)
        self.assertEqual(parent.command_log, [])
        self.assertEqual(child.command_log, [])
        self.assertEqual(parent.name, "parent")
        self.assertEqual(child.name, "parent")
        self.assertEqual(str(child), "parent")
        self.assertEqual(repr(child), "MockSource(parent)")

        child = MockSource(parent=parent, name="child")
        self.assertEqual(parent.name, "parent")
        self.assertEqual(child.name, "child")
        self.assertEqual(str(child), "child")
        self.assertEqual(repr(child), "MockSource(child)")

    def test_context_enter_parent(self) -> None:
        parent = MockSource(name="parent")
        child = MockSource(parent=parent, name="child")

        with parent as var:
            self.assertIs(var, parent)
            path1 = Path(parent.stack.enter_context(tempfile.NamedTemporaryFile()).name)
            path2 = Path(child.stack.enter_context(tempfile.NamedTemporaryFile()).name)
            self.assertTrue(path1.exists())
            self.assertTrue(path2.exists())
        self.assertFalse(path1.exists())
        self.assertFalse(path2.exists())

    def test_context_enter_child(self) -> None:
        parent = MockSource(name="parent")
        child = MockSource(parent=parent, name="child")

        with child as var:
            self.assertIs(var, child)
            path1 = Path(parent.stack.enter_context(tempfile.NamedTemporaryFile()).name)
            path2 = Path(child.stack.enter_context(tempfile.NamedTemporaryFile()).name)
            self.assertTrue(path1.exists())
            self.assertTrue(path2.exists())
        self.assertFalse(path1.exists())
        self.assertFalse(path2.exists())

    def test_git_clone_asis(self) -> None:
        """Test cloning without arguments."""

        with MockSource(name="source") as orig:
            src = orig._git_clone(repository=self.git.root.as_posix())
            self.assertIsInstance(src, Git)
            self.assertEqual(src.repo.active_branch.name, "main")
            self.assertFalse(src.readonly)
            self.assertNotEqual(src.path, self.git.root)
            self.assertTrue(src.path.exists)
            self.assertEqual(orig.command_log, [])
            self.assertEqual(src.command_log, [f"git -c advice.detachedHead=false clone --quiet {self.git.root}"])

            # Remote branches are recreated locally
            self.assertEqual(
                sorted(r.name for r in src.repo.refs if not r.name.startswith("origin/")), ["1.0", "devel", "main"]
            )

        self.assertFalse(src.path.exists())
        self.assertTrue(self.git.root.exists())

    def test_git_clone_branch(self) -> None:
        """Test cloning a branch."""

        origrepo = git.Repo(self.git.root)
        with MockSource(name="source") as orig:
            src = orig._git_clone(repository=self.git.root.as_posix(), branch="devel")
            self.assertIsInstance(src, Git)
            self.assertEqual(origrepo.active_branch.name, "main")
            self.assertEqual(src.repo.active_branch.name, "devel")
            self.assertFalse(src.readonly)
            self.assertNotEqual(src.path, self.git.root)
            self.assertTrue(src.path.exists)
            self.assertEqual(orig.command_log, [])
            self.assertEqual(
                src.command_log, [f"git -c advice.detachedHead=false clone --quiet {self.git.root} --branch devel"]
            )

        self.assertFalse(src.path.exists())
        self.assertTrue(self.git.root.exists())

    def test_git_clone_tag(self) -> None:
        """Test cloning a tag (detached head)."""

        origrepo = git.Repo(self.git.root)
        with MockSource(name="source") as orig:
            src = orig._git_clone(repository=self.git.root.as_posix(), branch="1.0")
            self.assertIsInstance(src, Git)
            self.assertEqual(origrepo.active_branch.name, "main")
            self.assertEqual(src.repo.active_branch.name, "moncic-ci")
            self.assertFalse(src.readonly)
            self.assertNotEqual(src.path, self.git.root)
            self.assertTrue(src.path.exists)
            self.assertEqual(orig.command_log, [])
            self.assertEqual(
                src.command_log,
                [
                    f"git -c advice.detachedHead=false clone --quiet {self.git.root} --branch 1.0",
                    "git checkout -b moncic-ci",
                ],
            )

        self.assertFalse(src.path.exists())
        self.assertTrue(self.git.root.exists())

    # Source.create*() methods are tested on each subclass test cases
