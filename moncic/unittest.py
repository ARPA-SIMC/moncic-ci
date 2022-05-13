from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import re
import shlex
import subprocess
import tempfile
from typing import (TYPE_CHECKING, Any, Callable, Dict, Generator, List,
                    Optional, Union)
from unittest import SkipTest

from moncic import imagestorage
from moncic.container import (Container, ContainerBase, ContainerConfig,
                              RunConfig, UserConfig)
from moncic.moncic import Moncic, MoncicConfig
from moncic.privs import ProcessPrivs
from moncic.system import System, SystemConfig, MaintenanceSystem
from moncic.btrfs import is_btrfs

if TYPE_CHECKING:
    from unittest import TestCase

    from moncic.distro import Distro

TEST_CHROOTS = ["centos7", "centos8", "rocky8", "fedora32", "fedora34", "fedora36", "buster", "bookworm", "bullseye"]

log = logging.getLogger(__name__)


class SudoTestSuite(ProcessPrivs):
    def needs_sudo(self):
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")


privs = SudoTestSuite()
privs.drop()


class MockImages(imagestorage.BtrfsImages):
    @contextlib.contextmanager
    def system(self, name: str) -> Generator[System, None, None]:
        system_config = SystemConfig.load(self.moncic.config, self.imagedir, name)
        system = MockSystem(self, system_config)
        system.attach_testcase(self.moncic.testcase)
        yield system

    @contextlib.contextmanager
    def maintenance_system(self, name: str) -> Generator[MaintenanceSystem, None, None]:
        system_config = SystemConfig.load(self.moncic.config, self.imagedir, name)
        system = MockMaintenanceSystem(self, system_config)
        system.attach_testcase(self.moncic.testcase)
        yield system


class MockMoncic(Moncic):
    def __init__(self, *, testcase: TestCase, **kw):
        super().__init__(**kw)
        self.testcase = testcase

    @contextlib.contextmanager
    def images(self) -> Generator[imagestorage.Images, None, None]:
        yield MockImages(self, self.config.imagedir)


def make_moncic(config: Optional[MoncicConfig] = None, testcase: Optional[TestCase] = None):
    """
    Create a Moncic instance configured to work with the test suite.

    If testcase is present, it will create a fullly mocked Moncic instance that
    will also create mock systems. Otherwise it will create a real Moncic
    instance configured to use test images
    """
    if config is not None:
        config = dataclasses.replace(config)
    else:
        config = MoncicConfig.load()

    if testcase is None:
        return Moncic(config=config, privs=privs)
    else:
        return MockMoncic(config=config, privs=privs, testcase=testcase)


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

    def assertPopFirst(self, cmd: Union[str, re.Pattern], **kwargs):
        actual_cmd, actual_kwargs = self.log.pop(0)

        if isinstance(cmd, str):
            self.testcase.assertEqual(actual_cmd, cmd)
        else:
            self.testcase.assertRegex(actual_cmd, cmd)

        self.testcase.assertEqual(actual_kwargs, kwargs)

    def assertLogEmpty(self):
        self.testcase.assertEqual(self.log, [])


class MockContainer(ContainerBase):
    def __init__(
            self, system: "MockSystem", config: ContainerConfig, instance_name: Optional[str] = None):
        super().__init__(system, config, instance_name)
        self.run_log = system.run_log

    def _start(self):
        self.started = True

    def _stop(self):
        self.started = False

    def forward_user(self, user: UserConfig, allow_maint: bool = False):
        self.run_log.append_forward_user(user)

    def run(self, command: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        self.run_log.append(command, {})
        return subprocess.CompletedProcess(command, 0, b'', b'')

    def run_script(self, body: str, config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        self.run_log.append_script(body)
        return subprocess.CompletedProcess(["script"], 0, b'', b'')

    def run_callable(
            self, func: Callable[[], Optional[int]], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        self.run_log.append_callable(func)
        return subprocess.CompletedProcess(func.__name__, 0, b'', b'')


class MockSystemMixin:
    def attach_testcase(self, testcase):
        self.run_log = MockRunLog(testcase)

    def create_container(
            self, instance_name: Optional[str] = None, config: Optional[ContainerConfig] = None) -> Container:
        config = self.container_config(config)
        return MockContainer(self, config, instance_name)

    def local_run(self, cmd: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        self.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b'', b'')

    def _update_cachedir(self):
        self.run_log.append_cachedir()


class MockSystem(MockSystemMixin, System):
    """
    Mock machine that just logs what is run and does nothing, useful for tests
    """
    pass


class MockMaintenanceSystem(MockSystemMixin, MaintenanceSystem):
    """
    Mock maintenance machine that just logs what is run and does nothing, useful for tests
    """
    pass


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
                    backing.truncate(100*1024*1024)
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
    def config(self, filesystem_type: Optional[str] = None) -> Generator[MoncicConfig]:
        if filesystem_type is None:
            filesystem_type = getattr(self, "DEFAULT_FILESYSTEM_TYPE", None)

        with workdir(filesystem_type=filesystem_type) as imagedir:
            yield MoncicConfig(
                    imagedir=imagedir,
                    imageconfdirs=[])

    @contextlib.contextmanager
    def mock_system(self, distro: Distro) -> Generator[MaintenanceSystem, None, None]:
        with self.config() as mconfig:
            config = SystemConfig(name="test", path=os.path.join(mconfig.imagedir, "test"), distro=distro.name)
            moncic = make_moncic(mconfig, testcase=self)
            with moncic.images() as images:
                system = MockMaintenanceSystem(images, config)
                system.attach_testcase(self)
                yield system
