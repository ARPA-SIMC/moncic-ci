from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.source.source import CommandLog, Source
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

    def test_context(self) -> None:
        parent = MockSource(name="parent")
        child = MockSource(parent=parent, name="child")

        with self.assertRaisesRegex(RuntimeError, "__enter__ called on non-root Source"):
            with child as var:
                pass

        with parent as var:
            self.assertIs(var, parent)
            path = Path(parent.stack.enter_context(tempfile.NamedTemporaryFile()).name)
            self.assertTrue(path.exists())
        self.assertFalse(path.exists())

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

    # def _git_clone(self, repository: str, branch: str | None = None) -> "Git":
    #     """
    #     Derive a Git source from this one, by cloning a git repository
    #     Clone this git repository into a temporary working directory.

    #     Return the path of the new cloned working directory
    #     """
    #     from .local import Git

    #     # Git checkout in a temporary directory
    #     workdir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
    #     command_log = CommandLog()

    #     clone_cmd = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", repository]
    #     if branch is not None:
    #         clone_cmd += ["--branch", branch]
    #     run(clone_cmd, cwd=workdir)
    #     command_log.add_command(*clone_cmd)

    #     # Look for the directory that git created
    #     paths = list(workdir.iterdir())
    #     if len(paths) != 1:
    #         raise RuntimeError("git clone created more than one entry in its current directory")

    #     new_path = paths[0]

    #     # Recreate remote branches
    #     repo = git.Repo(new_path)
    #     for ref in repo.remote().refs:
    #         name = ref.remote_head
    #         if name == "HEAD":
    #             continue
    #         if name in repo.refs:
    #             continue
    #         repo.create_head(name, ref)

    #     # If we cloned a detached head, create a local branch for it
    #     if repo.head.is_detached:
    #         branch = "moncic-ci"
    #         local_branch = repo.create_head(branch)
    #         local_branch.checkout()
    #         command_log.add_command("git", "checkout", "-b", branch)

    #     return Git(parent=self, path=new_path, repo=repo, branch=branch, readonly=False, command_log=command_log)


#     def _git_clone(self, repository: str, branch: str | None = None) -> "Git":

# Source.create() is tested on each subclass's test case
