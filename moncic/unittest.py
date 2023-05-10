from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import re
import shlex
import subprocess
import tempfile
from typing import (TYPE_CHECKING, Any, Callable, ContextManager, Dict,
                    Generator, List, Optional, Union)
from unittest import SkipTest, mock

from .container import RunConfig, UserConfig
from .runner import CompletedCallable
from .moncic import Moncic, MoncicConfig
from .system import MaintenanceSystem, SystemConfig
from .utils.btrfs import is_btrfs
from .utils.privs import ProcessPrivs

if TYPE_CHECKING:
    from moncic import imagestorage
    from moncic.distro import Distro

TEST_CHROOTS = ["centos7", "centos8", "rocky8", "rocky9", "fedora32", "fedora34", "fedora36", "fedora38", "buster", "bookworm", "bullseye"]

log = logging.getLogger(__name__)


class SudoTestSuite(ProcessPrivs):
    def needs_sudo(self):
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")


privs = SudoTestSuite()
privs.drop()


def make_moncic(config: Optional[MoncicConfig] = None):
    """
    Create a Moncic instance configured to work with the test suite.
    """
    if config is not None:
        # Use dataclasses.replace to make a copy
        config = dataclasses.replace(config)
    else:
        config = MoncicConfig.load()

    return Moncic(config=config, privs=privs)


class MockRunLog:
    def __init__(self, testcase):
        self.testcase = testcase
        self.log = []

    def append(self, cmd: List[str], kwargs: Dict[str, Any]):
        self.log.append((' '.join(shlex.quote(c) for c in cmd), kwargs))

    def append_script(self, body: str):
        self.log.append((f"script:{body}", {}))

    def append_callable(self, func: Callable[[], Optional[int]]):
        self.log.append((f"callable:{func.__name__}", {}))

    def append_forward_user(self, user: UserConfig):
        self.log.append((f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}", {}))

    def append_cachedir(self):
        self.log.append(("cachedir_tag:", {}))

    def assertPopFirstOptional(self, cmd: Union[str, re.Pattern], **kwargs):
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

    def assertPopFirst(self, cmd: Union[str, re.Pattern], **kwargs):
        actual_cmd, actual_kwargs = self.log.pop(0)

        if isinstance(cmd, str):
            self.testcase.assertEqual(actual_cmd, cmd)
        else:
            self.testcase.assertRegex(actual_cmd, cmd)

        self.testcase.assertEqual(actual_kwargs, kwargs)

    def assertLogEmpty(self):
        self.testcase.assertEqual(self.log, [])


@contextlib.contextmanager
def workdir(filesystem_type: Optional[str] = None):
    """
    Create a temporary working directory. If filesystem_type is set to one of
    the supported options, make sure it is backed by that given filessytem
    """
    if filesystem_type is None or filesystem_type == "default":
        # Default: let tempfile choose
        with tempfile.TemporaryDirectory() as imagedir:
            yield imagedir
    elif filesystem_type == "tmpfs":
        with tempfile.TemporaryDirectory() as imagedir:
            with privs.root():
                subprocess.run(["mount", "-t", "tmpfs", "none", imagedir], check=True)
            try:
                yield imagedir
            finally:
                with privs.root():
                    subprocess.run(["umount", imagedir], check=True)
    elif filesystem_type == "btrfs":
        with tempfile.TemporaryDirectory() as imagedir:
            if is_btrfs(imagedir):
                yield imagedir
            else:
                with tempfile.NamedTemporaryFile() as backing:
                    backing.truncate(1024*1024*1024)
                    subprocess.run(["mkfs.btrfs", backing.name], check=True)
                    with privs.root():
                        subprocess.run(["mount", "-t", "btrfs", backing.name, imagedir], check=True)
                    try:
                        yield imagedir
                    finally:
                        with privs.root():
                            subprocess.run(["umount", imagedir], check=True)


class DistroTestMixin:
    """
    TestCase mixin with extra common utility infrastructure to test Moncic-CI
    """
    @contextlib.contextmanager
    def config(self, filesystem_type: Optional[str] = None) -> Generator[MoncicConfig, None, None]:
        if filesystem_type is None:
            filesystem_type = getattr(self, "DEFAULT_FILESYSTEM_TYPE", None)

        with workdir(filesystem_type=filesystem_type) as imagedir:
            yield MoncicConfig(
                    imagedir=imagedir,
                    imageconfdirs=[],
                    deb_cache_dir=None)

    @contextlib.contextmanager
    def _mock_system(self, run_log: Optional[MockRunLog] = None) -> Generator[MockRunLog, None, None]:
        """
        Mock System objects to log operations instead of running them
        """
        rlog: MockRunLog
        if run_log is None:
            rlog = MockRunLog(self)
        else:
            rlog = run_log

        def _subvolume_replace_subvolume(self, path: str):
            rlog.append(["<replace>", self.path, path], {})
            self.path = path

        def _subvolume_local_run(self, cmd: List[str]) -> subprocess.CompletedProcess:
            rlog.append(cmd, {})
            return subprocess.CompletedProcess(cmd, 0, b'', b'')

        def _images_local_run(self, system_config: SystemConfig, cmd: List[str]) -> subprocess.CompletedProcess:
            rlog.append(cmd, {})
            return subprocess.CompletedProcess(cmd, 0, b'', b'')

        def _system_local_run(self, cmd: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
            rlog.append(cmd, {})
            return subprocess.CompletedProcess(cmd, 0, b'', b'')

        def _update_cachedir(self):
            rlog.append_cachedir()

        with mock.patch("moncic.utils.btrfs.Subvolume.replace_subvolume", new=_subvolume_replace_subvolume):
            with mock.patch("moncic.utils.btrfs.Subvolume.local_run", new=_subvolume_local_run):
                with mock.patch("moncic.imagestorage.Images.local_run", new=_images_local_run):
                    with mock.patch("moncic.system.System.local_run", new=_system_local_run):
                        with mock.patch("moncic.system.MaintenanceSystem.local_run", new=_system_local_run):
                            with mock.patch("moncic.system.MaintenanceSystem._update_cachedir", new=_update_cachedir):
                                yield rlog

    @contextlib.contextmanager
    def _mock_container(self, run_log: Optional[MockRunLog] = None) -> Generator[MockRunLog, None, None]:
        """
        Mock System objects to log operations instead of running them
        """
        rlog: MockRunLog
        if run_log is None:
            rlog = MockRunLog(self)
        else:
            rlog = run_log

        def _start(self):
            self.started = True

        def _stop(self):
            self.started = False

        def _forward_user(self, user: UserConfig, allow_maint: bool = False):
            rlog.append_forward_user(user)

        def _run(self, command: List[str], config: Optional[RunConfig] = None) -> CompletedCallable:
            rlog.append(command, {})
            return CompletedCallable(command, 0, b'', b'')

        def _run_script(self, body: str, config: Optional[RunConfig] = None) -> CompletedCallable:
            rlog.append_script(body)
            return CompletedCallable(["script"], 0, b'', b'')

        def _run_callable(
                self, func: Callable[[], Optional[int]],
                config: Optional[RunConfig] = None) -> CompletedCallable:
            rlog.append_callable(func)
            return CompletedCallable(func.__name__, 0, b'', b'')

        with mock.patch("moncic.container.NspawnContainer._start", new=_start):
            with mock.patch("moncic.container.NspawnContainer._stop", new=_stop):
                with mock.patch("moncic.container.NspawnContainer.forward_user", new=_forward_user):
                    with mock.patch("moncic.container.NspawnContainer.run", new=_run):
                        with mock.patch("moncic.container.NspawnContainer.run_script", new=_run_script):
                            with mock.patch("moncic.container.NspawnContainer.run_callable", new=_run_callable):
                                yield rlog

    @contextlib.contextmanager
    def mock(self, system: bool = True, container: bool = True) -> Generator[MockRunLog, None, None]:
        """
        Mock System or Container objects
        """
        run_log = MockRunLog(self)

        msys: ContextManager[MockRunLog]
        if system:
            msys = self._mock_system(run_log)
        else:
            msys = contextlib.nullcontext(run_log)

        mcont: ContextManager[MockRunLog]
        if container:
            mcont = self._mock_container(run_log)
        else:
            mcont = contextlib.nullcontext(run_log)

        with msys as run_log:
            with mcont as run_log:
                yield run_log

    @contextlib.contextmanager
    def make_images(self, distro: Distro) -> Generator[imagestorage.Images, None, None]:
        with self.config() as mconfig:
            moncic = make_moncic(mconfig)

            def _load(mconfig: MoncicConfig, imagedir: str, name: str):
                return SystemConfig(name=name, path=os.path.join(mconfig.imagedir, "test"), distro=distro.name)

            with mock.patch("moncic.system.SystemConfig.load", new=_load):
                with moncic.session() as session:
                    yield session.images

    @contextlib.contextmanager
    def make_system(self, distro: Distro) -> Generator[MaintenanceSystem, None, None]:
        with self.make_images(distro) as images:
            with images.maintenance_system("test") as system:
                yield system
