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

from moncic.system import System, Config
from moncic.container import Container, ContainerBase, ContainerConfig, RunConfig, UserConfig
from moncic.moncic import Moncic
from moncic.privs import ProcessPrivs

if TYPE_CHECKING:
    from moncic.distro import Distro

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


class DistroTestMixin:
    @contextlib.contextmanager
    def mock_system(self, distro: Distro):
        with tempfile.TemporaryDirectory() as workdir:
            config = Config(name="test", path=os.path.join(workdir, "test"), distro=distro.name)
            system = MockSystem(Moncic(imagedir=workdir), config)
            system.attach_testcase(self)
            yield system
