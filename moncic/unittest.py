import contextlib
import copy
import logging
import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, ClassVar
from unittest import SkipTest, TestCase

from . import context
from .mock.session import MockSession
from .moncic import Moncic, MoncicConfig
from .runner import UserConfig
from .utils.btrfs import is_btrfs
from .utils.privs import ProcessPrivs
from .utils.script import Script

TEST_CHROOTS = [
    "centos7",
    "centos8",
    "rocky8",
    "rocky9",
    "fedora32",
    "fedora34",
    "fedora36",
    "fedora38",
    "fedora40",
    "fedora42",
    "buster",
    "bookworm",
    "bullseye",
]

log = logging.getLogger(__name__)


class SudoTestSuite(ProcessPrivs):
    def needs_sudo(self):
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")


class MockRunLog:
    def __init__(self, testcase):
        self.testcase = testcase
        self.log = []

    def append_action(self, action: str) -> None:
        self.log.append((action, {}))

    def append(self, cmd: list[str], kwargs: dict[str, Any]):
        self.log.append((shlex.join(cmd), kwargs))

    def append_script(self, script: Script | str):
        if isinstance(script, Script):
            self.log.append((script.title, {"script": script}))
        else:
            self.log.append((f"script:{script}", {}))

    def append_callable(self, func: Callable[[], int | None]):
        self.log.append((f"callable:{func.__name__}", {}))

    def append_forward_user(self, user: UserConfig):
        self.log.append((f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}", {}))

    def append_cachedir(self):
        self.log.append(("cachedir_tag:", {}))

    def assertPopFirstOptional(self, cmd: str | re.Pattern, **kwargs):
        actual_cmd, actual_kwargs = self.log[0]

        skip = False
        if isinstance(cmd, str):
            if not actual_cmd.startswith(cmd.split()[0]):
                skip = True
            else:
                self.testcase.assertEqual(actual_cmd, cmd)
        else:
            if not cmd.search(actual_cmd):
                skip = True
            else:
                self.testcase.assertRegex(actual_cmd, cmd)

        if not skip:
            self.log.pop(0)
            self.testcase.assertEqual(actual_kwargs, kwargs)

    def assertPopFirst(self, cmd: str | re.Pattern, **kwargs):
        actual_cmd, actual_kwargs = self.log.pop(0)

        if isinstance(cmd, str):
            self.testcase.assertEqual(actual_cmd, cmd)
        else:
            self.testcase.assertRegex(actual_cmd, cmd)

        self.testcase.assertEqual(actual_kwargs, kwargs)

    def assertLogEmpty(self):
        self.testcase.assertEqual(self.log, [])


class MoncicTestCase(TestCase):
    old_privs: ClassVar[ProcessPrivs]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        privs = SudoTestSuite()
        privs.drop()
        cls.old_privs = context.privs
        context.privs = privs

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
            return imagedir
        elif filesystem_type == "btrfs":
            imagedir = self.tempdir()
            if is_btrfs(imagedir):
                return imagedir
            backing = self.enterContext(tempfile.NamedTemporaryFile())
            backing.truncate(1024 * 1024 * 1024)
            subprocess.run(["mkfs.btrfs", backing.name], check=True)
            self.enterContext(self.mount("btrfs", backing.name, imagedir))
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

        config.imageconfdirs = [self.tempdir()]

        if config.imagedir is None or not config.imagedir.is_dir():
            imagedir = Path(self.enterContext(tempfile.TemporaryDirectory()))
            config.imagedir = Path(imagedir)
            return Moncic(config=config)
        else:
            return Moncic(config=config)

    def mock_session(self, moncic: Moncic | None = None) -> MockSession:
        if moncic is None:
            moncic = self.moncic()
        return MockSession(moncic, MockRunLog(self))
