import contextlib
import copy
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

from . import context
from .images import Images
from .mock.session import MockSession, MockRunLog, MockMoncic
from .moncic import Moncic, MoncicConfig
from .utils.btrfs import is_btrfs
from .utils.privs import ProcessPrivs
from .utils.script import Script

log = logging.getLogger(__name__)


class SudoTestSuite(ProcessPrivs):
    @override
    def needs_sudo(self) -> None:
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")


class TestCase(unittest.TestCase):
    """TestCase extended with moncic-ci specific assertions."""

    def assertRunLogPopFirstOptional(self, run_log: MockRunLog, cmd: str | re.Pattern[str], **kwargs: Any) -> None:
        actual_cmd, actual_kwargs = run_log.log[0]

        skip = False
        if isinstance(cmd, str):
            if not actual_cmd.startswith(cmd.split()[0]):
                skip = True
            else:
                self.assertEqual(actual_cmd, cmd)
        else:
            if not cmd.search(actual_cmd):
                skip = True
            else:
                self.assertRegex(actual_cmd, cmd)

        if not skip:
            run_log.log.pop(0)
            self.assertEqual(actual_kwargs, kwargs)

    def assertRunLogPopFirst(self, run_log: MockRunLog, cmd: str | re.Pattern[str], **kwargs: Any) -> None:
        actual_cmd, actual_kwargs = run_log.log.pop(0)

        if isinstance(cmd, str):
            self.assertEqual(actual_cmd, cmd)
        else:
            self.assertRegex(actual_cmd, cmd)

        self.assertEqual(actual_kwargs, kwargs)

    def assertRunLogPopUntil(self, run_log: MockRunLog, cmd: str | re.Pattern[str], **kwargs: Any) -> None:
        matched = False
        while run_log.log:
            actual_cmd, actual_kwargs = run_log.log.pop(0)
            if isinstance(cmd, str):
                matched = actual_cmd == cmd
            else:
                matched = bool(cmd.search(actual_cmd))
            matched = matched and actual_kwargs == kwargs

            if matched:
                break

        if not matched:
            self.fail(f"{cmd} not found in run log")

    def assertRunLogPopScript(self, run_log: MockRunLog, title: str) -> Script:
        actual_cmd, actual_kwargs = run_log.log.pop(0)
        self.assertEqual(actual_cmd, title)
        script = actual_kwargs["script"]
        assert isinstance(script, Script)
        return script

    def assertRunLogEmpty(self, run_log: MockRunLog) -> None:
        self.assertEqual(run_log.log, [])


class MockMoncicTestCase(TestCase):
    """Test case instantiating a MockMoncic per test."""

    @override
    def setUp(self) -> None:
        """Set up a MockMoncic for this test case."""
        super().setUp()
        self.imageconfdir = self.get_imageconfdir()
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
