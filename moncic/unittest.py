import contextlib
import copy
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any, ClassVar, override
import unittest
from unittest import SkipTest, mock

from moncic import context
from moncic.images import Images
from moncic.mock.session import MockSession, MockRunLog, MockMoncic, RunLogEntry
from moncic.moncic import Moncic, MoncicConfig
from moncic.utils.btrfs import is_btrfs
from moncic.utils.privs import ProcessPrivs
from moncic.utils.script import Script

log = logging.getLogger(__name__)


class SudoTestSuite(ProcessPrivs):
    @override
    def needs_sudo(self) -> None:
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")


class RunLogMatcher:
    def __init__(self, testcase: unittest.TestCase, log: RunLogEntry) -> None:
        self.testcase = testcase
        self.log = log

    def _tentative_match(self, entry: RunLogEntry, name: str | re.Pattern[str]) -> bool:
        """Check if an entry may match the given name."""
        if isinstance(name, str):
            return entry.name.startswith(name.split()[0])
        else:
            return bool(name.search(entry.name))

    def assertEntryMatches(self, entry: RunLogEntry, name: str | re.Pattern[str], **kwargs: Any) -> None:
        """Test the entry for an exact match."""
        if isinstance(name, str):
            self.testcase.assertEqual(entry.name, name)
        else:
            self.testcase.assertRegex(entry.name, name)
        self.testcase.assertEqual(entry.data, kwargs)

    def assertPopFirstOptional(self, name: str | re.Pattern[str], **kwargs: Any) -> RunLogEntry | None:
        """
        Optionally match the first run log and return it.

        :returns: the matched entry, or None if the name does not match
        """
        entry = self.log.entries[0]
        if not self._tentative_match(entry, name):
            return None
        self.log.entries.pop(0)
        self.assertEntryMatches(entry, name, **kwargs)
        return entry

    def assertPopFirst(self, name: str | re.Pattern[str], **kwargs: Any) -> RunLogEntry:
        """Match the first run log and return it."""
        entry = self.log.entries.pop(0)
        self.assertEntryMatches(entry, name, **kwargs)
        return entry

    def assertPopUntil(self, name: str | re.Pattern[str], **kwargs: Any) -> RunLogEntry:
        """Skip entries until the name matches, and return it."""
        matched = False
        while self.log.entries and not matched:
            entry = self.log.entries.pop(0)
            matched = self._tentative_match(entry, name)

        if not matched:
            self.testcase.fail(f"{name} not found in run log")

        self.assertEntryMatches(entry, name, **kwargs)
        return entry

    def assertPopScript(self, title: str) -> Script:
        """Treat the first entry as a script, match its title and return it."""
        entry = self.log.entries.pop(0)
        self.testcase.assertEqual(entry.name, title)
        script = entry.data["script"]
        assert isinstance(script, Script)
        return script

    def assertEmpty(self) -> None:
        """Ensure the run log is empty."""
        self.testcase.assertEqual(self.log.entries, [])


class TestCase(unittest.TestCase):
    """TestCase extended with moncic-ci specific assertions."""

    @contextlib.contextmanager
    def match_run_log(self, run_log: MockRunLog | RunLogEntry) -> Generator[RunLogMatcher, None, None]:
        """Create a destroying matcher for the given run log."""
        if isinstance(run_log, MockRunLog):
            yield RunLogMatcher(self, run_log.current)
        else:
            yield RunLogMatcher(self, run_log)

    def assertRunLogEmpty(self, run_log: MockRunLog | RunLogEntry) -> None:
        """Ensure the run log is empty."""
        with self.match_run_log(run_log) as m:
            m.assertEmpty()


class MockMoncicTestCase(TestCase):
    """Test case instantiating a MockMoncic per test."""

    @override
    def setUp(self) -> None:
        """Set up a MockMoncic for this test case."""
        super().setUp()
        self.imageconfdir: Path | None = self.get_imageconfdir()
        self.config = self.make_moncic_config()
        self.moncic = MockMoncic(self.config)

    def get_imageconfdir(self) -> Path | None:
        """Return the imageconfdir to use for this test case."""
        return None

    def make_moncic_config(self) -> MoncicConfig:
        """Return the Moncic config to use for this test case."""
        config = MoncicConfig()
        config.imageconfdirs = [self.imageconfdir] if self.imageconfdir else []
        config.deb_cache_dir = None
        return config


class CLITestCase(MockMoncicTestCase):
    """Test case for CLI commands."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.session = MockSession(self.moncic)
        self.enterContext(mock.patch("moncic.cli.moncic.Moncic", return_value=self.moncic))
        self.enterContext(mock.patch.object(self.moncic, "session", return_value=self.session))

    @override
    def get_imageconfdir(self) -> Path | None:
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def assertNoStderr(self, res: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(res.stderr, "")

    def call(self, *args: str) -> subprocess.CompletedProcess[str]:
        from moncic.__main__ import main

        orig_argv = sys.argv
        sys.argv = list(args)
        stdout = io.StringIO()
        stderr = io.StringIO()
        returnvalue: int | None = None
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                main()
                returnvalue = 0
            finally:
                sys.argv = orig_argv

        return subprocess.CompletedProcess(args, returnvalue, stdout.getvalue(), stderr.getvalue())


class MoncicTestCase(TestCase):
    old_privs: ClassVar[ProcessPrivs]

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        privs = SudoTestSuite()
        privs.drop()
        cls.old_privs = context.privs
        context.privs = privs

    @override
    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        context.privs = cls.old_privs

    @contextlib.contextmanager
    def mount(self, mount_type: str, src: str | Path, path: Path) -> Generator[None]:
        with context.privs.root():
            subprocess.run(["mount", "-t", mount_type, str(src), str(path)], check=True)
        try:
            yield None
        finally:
            with context.privs.root():
                subprocess.run(["umount", str(path)], check=True)

    def tempdir(self) -> Path:
        """Create a temporary directory."""
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def workdir(self, filesystem_type: str | None = None) -> Path:
        """
        Create a temporary working directory. If filesystem_type is set to one of
        the supported options, make sure it is backed by that given filessytem
        """
        if filesystem_type is None or filesystem_type == "default":
            # Default: let tempfile choose
            return self.tempdir()
        elif filesystem_type == "tmpfs":
            imagedir = self.tempdir()
            self.enterContext(self.mount("tmpfs", "none", imagedir))
            with context.privs.root():
                os.chown(imagedir, context.privs.user_uid, context.privs.user_gid)
            return imagedir
        elif filesystem_type == "btrfs":
            imagedir = self.tempdir()
            if is_btrfs(imagedir):
                return imagedir
            backing = self.enterContext(tempfile.NamedTemporaryFile())
            backing.truncate(1024 * 1024 * 1024)
            subprocess.run(["mkfs.btrfs", backing.name], check=True)
            self.enterContext(self.mount("btrfs", backing.name, imagedir))
            with context.privs.root():
                os.chown(imagedir, context.privs.user_uid, context.privs.user_gid)
            return imagedir
        else:
            raise NotImplementedError(f"unsupported filesystem type {filesystem_type!r}")

    def config(self, filesystem_type: str | None = None) -> MoncicConfig:
        if filesystem_type is None:
            filesystem_type = getattr(self, "DEFAULT_FILESYSTEM_TYPE", None)
        imagedir = self.workdir(filesystem_type)
        res = MoncicConfig()
        res.imagedir = imagedir
        res.imageconfdirs = []
        res.deb_cache_dir = None
        return res

    def moncic(self, config: MoncicConfig | None = None) -> Moncic:
        """
        Create a Moncic instance configured to work with the test suite.
        """
        if config is not None:
            # Use dataclasses.replace to make a copy
            config = copy.deepcopy(config)
        else:
            config = MoncicConfig.load()

        if config.imagedir is None or not config.imagedir.is_dir():
            imagedir = Path(self.enterContext(tempfile.TemporaryDirectory()))
            config.imagedir = Path(imagedir)
            return Moncic(config=config)
        else:
            return Moncic(config=config)

    def mock_session(self, moncic: Moncic | None = None, images_class: type[Images] | None = None) -> MockSession:
        if moncic is None:
            moncic = self.moncic()
        return MockSession(moncic, bootstrapper_cls=images_class)


def add_testcase(module_name: str, test_case: type[MoncicTestCase]) -> None:
    """Add a test case class to the named test module."""
    this_module = sys.modules[module_name]
    test_case.__module__ = module_name
    setattr(this_module, test_case.__name__, test_case)
