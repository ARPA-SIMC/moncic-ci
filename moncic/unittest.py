from __future__ import annotations
import contextlib
import logging
import os
import re
import shlex
import subprocess
import tempfile
from typing import Callable, Optional, List, Dict, Any, Union, TYPE_CHECKING
from unittest import SkipTest

from moncic.system import System, SystemConfig
from moncic.container import Container, ContainerBase, ContainerConfig, RunConfig, UserConfig
from moncic.moncic import Moncic, MoncicConfig
from moncic.privs import ProcessPrivs

if TYPE_CHECKING:
    from moncic.distro import Distro
    from unittest import TestCase

TEST_CHROOTS = ["centos7", "centos8", "rocky8", "fedora32", "fedora34", "buster", "bookworm", "bullseye"]

log = logging.getLogger(__name__)


class SudoTestSuite(ProcessPrivs):
    def needs_sudo(self):
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")

    def regain(self):
        """
        Regain root privileges
        """
        if not self.dropped:
            return
        self.needs_sudo()
        super().regain()


privs = SudoTestSuite()
privs.drop()


class MockMoncic(Moncic):
    def __init__(self, *, testcase: TestCase, **kw):
        super().__init__(**kw)
        self.testcase = testcase

    def create_system(self, name_or_path: str) -> System:
        res = super().create_system(name_or_path)
        res.attach_testcase(self.testcase)
        return res


def make_moncic(imagedir: str, testcase: Optional[TestCase] = None):
    """
    Create a Moncic instance configured to work with the test suite.

    If testcase is present, it will create a fullly mocked Moncic instance that
    will also create mock systems. Otherwise it will create a real Moncic
    instance configured to use test images
    """
    config = MoncicConfig(imagedir=imagedir)
    if testcase is None:
        return Moncic(config=config, privs=privs)
    else:
        return MockMoncic(config=config, privs=privs, system_class=MockSystem, testcase=testcase)


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
            self, system: "MockSystem", instance_name: Optional[str] = None, config: Optional[ContainerConfig] = None):
        super().__init__(system, instance_name, config)
        self.run_log = system.run_log

    def _start(self):
        self.started = True

    def _stop(self):
        self.started = False

    def forward_user(self, user: UserConfig):
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


class MockSystem(System):
    """
    Mock machine that just logs what is run and does nothing, useful for tests
    """
    def attach_testcase(self, testcase):
        self.run_log = MockRunLog(testcase)

    def local_run(self, cmd: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        self.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b'', b'')

    def create_container(
            self, instance_name: Optional[str] = None, config: Optional[ContainerConfig] = None) -> Container:
        return MockContainer(self, instance_name, config)

    def _update_cachedir(self):
        self.run_log.append_cachedir()


class DistroTestMixin:
    @contextlib.contextmanager
    def mock_system(self, distro: Distro):
        with tempfile.TemporaryDirectory() as workdir:
            config = SystemConfig(name="test", path=os.path.join(workdir, "test"), distro=distro.name)
            system = MockSystem(make_moncic(imagedir=workdir, testcase=self), config)
            system.attach_testcase(self)
            yield system
