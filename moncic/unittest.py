from __future__ import annotations
import contextlib
import logging
import os
import re
import shlex
import tempfile
from typing import Callable, Optional, List, Dict, Any, Union, TYPE_CHECKING
from unittest import SkipTest

from moncic.system import System, Config
from moncic.run import RunningSystem, UpdateMixin
from moncic.bootstrap import Bootstrapper

if TYPE_CHECKING:
    from moncic.distro import Distro

TEST_CHROOTS = ["centos7", "centos8", "fedora32", "fedora34"]

log = logging.getLogger(__name__)


class ProcessPrivs:
    """
    Drop root privileges and regain them only when needed
    """
    def __init__(self):
        self.orig_uid, self.orig_euid, self.orig_suid = os.getresuid()
        self.orig_gid, self.orig_egid, self.orig_sgid = os.getresgid()

        self.have_sudo = "SUDO_UID" in os.environ

        if self.have_sudo:
            self.user_uid = int(os.environ["SUDO_UID"])
            self.user_gid = int(os.environ["SUDO_GID"])
        else:
            self.user_uid = self.orig_uid
            self.user_gid = self.orig_gid

        self.dropped = not self.have_sudo

    def needs_sudo(self):
        if not self.have_sudo:
            raise SkipTest("Tests need to be run under sudo")

    def drop(self):
        """
        Drop root privileges
        """
        if self.dropped:
            return
        os.setresgid(self.user_gid, self.user_gid, 0)
        os.setresuid(self.user_uid, self.user_uid, 0)
        self.dropped = True

    def regain(self):
        """
        Regain root privileges
        """
        if not self.dropped:
            return
        self.needs_sudo()
        os.setresuid(self.orig_suid, self.orig_suid, self.user_uid)
        os.setresgid(self.orig_sgid, self.orig_sgid, self.user_gid)
        self.dropped = False

    @contextlib.contextmanager
    def root(self):
        """
        Regain root privileges for the duration of this context manager
        """
        if not self.dropped:
            yield
        else:
            self.regain()
            try:
                yield
            finally:
                self.drop()

    @contextlib.contextmanager
    def user(self):
        """
        Drop root privileges for the duration of this context manager
        """
        if self.dropped:
            yield
        else:
            self.drop()
            try:
                yield
            finally:
                self.regain()


privs = ProcessPrivs()
privs.drop()


class MockRunLog:
    def __init__(self, testcase):
        self.testcase = testcase
        self.log = []

    def append(self, cmd: List[str], kwargs: Dict[str, Any]):
        self.log.append((' '.join(shlex.quote(c) for c in cmd), kwargs))

    def append_callable(self, func: Callable[[], Optional[int]]):
        self.log.append((f"callable:{func.__name__}", {}))

    def assertPopFirst(self, cmd: Union[str, re.compile], **kwargs):
        actual_cmd, actual_kwargs = self.log.pop(0)

        if isinstance(cmd, str):
            self.testcase.assertEqual(actual_cmd, cmd)
        else:
            self.testcase.assertRegex(actual_cmd, cmd)

        self.testcase.assertEqual(actual_kwargs, kwargs)

    def assertLogEmpty(self):
        self.testcase.assertEqual(self.log, [])


class MockRunningSystem(UpdateMixin, RunningSystem):
    def start(self):
        if self.started:
            return
        self.started = True

    def terminate(self):
        if not self.started:
            return
        self.started = False

    def run(self, command: List[str]) -> Dict[str, Any]:
        self.system.run_log.append(command, {})
        return {
            "stdout": b'',
            "stderr": b'',
            "returncode": 0,
        }

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        self.system.run_log.append_callable(func)
        return 0


class MockBootstrapper(Bootstrapper):
    def run(self, cmd: List[str], **kw) -> Dict[str, Any]:
        self.system.run_log.append(cmd, {})
        return {
            "stdout": b'',
            "stderr": b'',
            "returncode": 0,
        }


class MockSystem(System):
    """
    Mock machine that just logs what is run and does nothing, useful for tests
    """
    def attach_testcase(self, testcase):
        self.run_log = MockRunLog(testcase)

    def create_ephemeral_run(self, instance_name: Optional[str] = None) -> RunningSystem:
        return MockRunningSystem(self)

    def create_maintenance_run(self, instance_name: Optional[str] = None) -> RunningSystem:
        return MockRunningSystem(self)

    def create_bootstrapper(self) -> Bootstrapper:
        return MockBootstrapper(self)


class DistroTestMixin:
    @contextlib.contextmanager
    def mock_system(self, distro: Distro):
        with tempfile.TemporaryDirectory() as workdir:
            config = Config(name="test", path=os.path.join(workdir, "test"), distro=distro.name)
            system = MockSystem(config)
            system.attach_testcase(self)
            yield system
