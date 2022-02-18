from __future__ import annotations
import unittest
import sys
import secrets
import time
from moncic.unittest import privs, TEST_CHROOTS
from moncic.system import System
from moncic.moncic import Moncic


class RunTestCase:
    distro_name: str

    def get_system(self) -> System:
        moncic = Moncic(imagedir="images")
        return moncic.create_system(self.distro_name)

    def test_true(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                container.run(["/usr/bin/true"])

    def test_sleep(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                start = time.time()
                container.run(["/usr/bin/sleep", "0.1"])
                # Check that 0.1 seconds have passed
                self.assertGreaterEqual(time.time() - start, 0.1)

    def test_stdout(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                res = container.run(["/usr/bin/echo", "test"])
                self.assertEqual(res.stdout, b"test\n")
                self.assertEqual(res.stderr, b"")

    def test_env(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                res = container.run(["/bin/sh", "-c", "echo $HOME"])
                self.assertEqual(res.stdout, b"/root\n")
                self.assertEqual(res.stderr, b"")

    def test_callable(self):
        token = secrets.token_bytes(8)
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                def test_function():
                    with open("/tmp/token", "wb") as out:
                        out.write(token)

                res = container.run_callable(test_function)
                self.assertEqual(res.stdout, b'')
                self.assertEqual(res.stderr, b'')
                self.assertEqual(res.returncode, 0)

                res = container.run(["/usr/bin/cat", "/tmp/token"])
                self.assertEqual(res.stdout, token)
                self.assertEqual(res.stderr, b"")
                self.assertEqual(res.returncode, 0)

    def test_callable_prints(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                def test_function():
                    print("stdout")
                    print("stderr", file=sys.stderr)

                res = container.run_callable(test_function)
                self.assertEqual(res.stdout, b'stdout\n')
                self.assertEqual(res.stderr, b'stderr\n')
                self.assertEqual(res.returncode, 0)

    def test_multi_maint_runs(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                res = container.run(["/bin/echo", "1"])
                self.assertEqual(res.stdout, b"1\n")
                self.assertEqual(res.stderr, b"")
            with system.create_container() as container:
                res = container.run(["/bin/echo", "2"])
                self.assertEqual(res.stdout, b"2\n")
                self.assertEqual(res.stderr, b"")

    def test_run_script(self):
        system = self.get_system()
        with privs.root():
            with system.create_container() as container:
                res = container.run_script("#!/bin/sh\nA=test\necho $A\nexit 1\n", check=False)
                self.assertEqual(res.stdout, b"test\n")
                self.assertEqual(res.stderr, b"")
                self.assertEqual(res.returncode, 1)


# Create an instance of RunTestCase for each distribution in TEST_CHROOTS.
# The test cases will be named Test$DISTRO. For example:
#   TestCentos7, TestCentos8, TestFedora32, TestFedora34
this_module = sys.modules[__name__]
for distro_name in TEST_CHROOTS:
    cls_name = "Test" + distro_name.capitalize()
    test_case = type(cls_name, (RunTestCase, unittest.TestCase), {"distro_name": distro_name})
    test_case.__module__ = __name__
    setattr(this_module, cls_name, test_case)
