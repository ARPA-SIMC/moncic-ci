from __future__ import annotations
import unittest
import sys
import os
import secrets
from moncic.unittest import privs, TEST_CHROOTS
from moncic.system import System


class RunTestCase:
    def get_system(self) -> System:
        return System(os.path.join("images", self.distro_name), name=self.distro_name)

    def test_true(self):
        system = self.get_system()
        with privs.root():
            with system.create_ephemeral_run() as run:
                run.run(["/usr/bin/true"])

    def test_sleep(self):
        system = self.get_system()
        with privs.root():
            with system.create_ephemeral_run() as run:
                run.run(["/usr/bin/sleep", "0.1"])

    def test_stdout(self):
        system = self.get_system()
        with privs.root():
            with system.create_ephemeral_run() as run:
                res = run.run(["/usr/bin/echo", "test"])
                self.assertEqual(res["stdout"], b"test\n")
                self.assertEqual(res["stderr"], b"")

    def test_env(self):
        system = self.get_system()
        with privs.root():
            with system.create_ephemeral_run() as run:
                res = run.run(["/bin/sh", "-c", "echo $HOME"])
                self.assertEqual(res["stdout"], b"/root\n")
                self.assertEqual(res["stderr"], b"")

    def test_callable(self):
        token = secrets.token_bytes(8)
        system = self.get_system()
        with privs.root():
            with system.create_ephemeral_run() as run:
                def test_function():
                    with open("/tmp/token", "wb") as out:
                        out.write(token)

                self.assertEqual(run.run_callable(test_function), {
                    'stdout': b'',
                    'stderr': b'',
                    'returncode': 0,
                })

                res = run.run(["/usr/bin/cat", "/tmp/token"])
                self.assertEqual(res["stdout"], token)
                self.assertEqual(res["stderr"], b"")

    def test_callable_prints(self):
        system = self.get_system()
        with privs.root():
            with system.create_ephemeral_run() as run:
                def test_function():
                    print("stdout")
                    print("stderr", file=sys.stderr)

                self.assertEqual(run.run_callable(test_function), {
                    'stdout': b'stdout\n',
                    'stderr': b'stderr\n',
                    'returncode': 0,
                })


# Create an instance of RunTestCase for each distribution in TEST_CHROOTS.
# The test cases will be named Test$DISTRO. For example:
#   TestCentos7, TestCentos8, TestFedora32, TestFedora34
this_module = sys.modules[__name__]
for distro_name in TEST_CHROOTS:
    cls_name = "Test" + distro_name.capitalize()
    test_case = type(cls_name, (RunTestCase, unittest.TestCase), {"distro_name": distro_name})
    test_case.__module__ = __name__
    setattr(this_module, cls_name, test_case)
