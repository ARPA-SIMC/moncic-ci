from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import time
import unittest

from moncic.container import ContainerConfig, RunConfig, UserConfig
from moncic.unittest import TEST_CHROOTS, make_moncic, privs


class RunTestCase:
    distro_name: str

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.exit_stack = contextlib.ExitStack()
        # cls.workdir = cls.exit_stack.enter_context(tempfile.TemporaryDirectory())
        # cls.moncic = make_moncic(imagedir=cls.workdir)
        cls.moncic = make_moncic()
        cls.images = cls.exit_stack.enter_context(cls.moncic.images())

    @classmethod
    def tearDownClass(cls):
        cls.exit_stack.close()
        cls.images = None
        cls.moncic = None
        # cls.workdir = None
        cls.exit_stack = None
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        with privs.root():
            if self.distro_name not in self.images.list_images():
                raise unittest.SkipTest(f"Image {self.distro_name} not available")
            self.system = self.images.create_system(self.distro_name)
            if not os.path.exists(self.system.path):
                raise unittest.SkipTest(f"Image {self.distro_name} has not been bootstrapped")

    def tearDown(self):
        self.system = None
        super().tearDown()

    def test_true(self):
        with privs.root():
            with self.system.create_container() as container:
                container.run(["/usr/bin/true"])

    def test_sleep(self):
        with privs.root():
            with self.system.create_container() as container:
                start = time.time()
                container.run(["/usr/bin/sleep", "0.1"])
                # Check that 0.1 seconds have passed
                self.assertGreaterEqual(time.time() - start, 0.1)

    def test_stdout(self):
        with privs.root():
            with self.system.create_container() as container:
                res = container.run(["/usr/bin/echo", "test"])
                self.assertEqual(res.stdout, b"test\n")
                self.assertEqual(res.stderr, b"")

    def test_env(self):
        with privs.root():
            with self.system.create_container() as container:
                res = container.run(["/bin/sh", "-c", "echo $HOME"])
                self.assertEqual(res.stdout, b"/root\n")
                self.assertEqual(res.stderr, b"")

    def test_callable(self):
        token = secrets.token_bytes(8)
        with privs.root():
            with self.system.create_container() as container:
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
        with privs.root():
            with self.system.create_container() as container:
                def test_function():
                    print("stdout")
                    print("stderr", file=sys.stderr)

                res = container.run_callable(test_function)
                self.assertEqual(res.stdout, b'stdout\n')
                self.assertEqual(res.stderr, b'stderr\n')
                self.assertEqual(res.returncode, 0)

    def test_multi_maint_runs(self):
        with privs.root():
            with self.system.create_container() as container:
                res = container.run(["/bin/echo", "1"])
                self.assertEqual(res.stdout, b"1\n")
                self.assertEqual(res.stderr, b"")
            with self.system.create_container() as container:
                res = container.run(["/bin/echo", "2"])
                self.assertEqual(res.stdout, b"2\n")
                self.assertEqual(res.stderr, b"")

    def test_run_script(self):
        with privs.root():
            with self.system.create_container() as container:
                res = container.run_script("#!/bin/sh\nA=test\necho $A\nexit 1\n", config=RunConfig(check=False))
                self.assertEqual(res.stdout, b"test\n")
                self.assertEqual(res.stderr, b"")
                self.assertEqual(res.returncode, 1)

    def test_forward_user(self):
        def get_user():
            print(json.dumps(UserConfig.from_current()))

        user = UserConfig.from_sudoer()

        # By default, things are run as root
        container_config = ContainerConfig()
        with privs.root():
            with self.system.create_container(config=container_config) as container:
                res = container.run_callable(get_user)
                u = UserConfig(*json.loads(res.stdout))
                self.assertEqual(res.stderr, b"")
                self.assertEqual(u, UserConfig("root", 0, "root", 0))

                # Running with another user fails as it does not exist in the
                # container
                with self.assertRaises(subprocess.CalledProcessError) as e:
                    res = container.run_callable(get_user, config=RunConfig(user=user))
                self.assertRegex(e.exception.stderr.decode(), "RuntimeError: container has no user 1000 'enrico'")

        container_config = ContainerConfig(forward_user=True)
        with privs.root():
            with self.system.create_container(config=container_config) as container:
                res = container.run_callable(get_user)
                u = UserConfig(*json.loads(res.stdout))
                self.assertEqual(res.stderr, b"")
                self.assertEqual(u, UserConfig("root", 0, "root", 0))

                res = container.run_callable(get_user, config=RunConfig(user=user))
                u = UserConfig(*json.loads(res.stdout))
                self.assertEqual(res.stderr, b"")
                self.assertEqual(u, user)

                res = container.run_script("#!/bin/sh\n/bin/true\n", config=RunConfig(user=user))
                self.assertEqual(res.stdout, b"")
                self.assertEqual(res.stderr, b"")

    def test_forward_user_workdir(self):
        user = UserConfig.from_sudoer()

        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig(workdir=workdir, forward_user=True)
            with privs.root():
                with self.system.create_container(config=container_config) as container:
                    res = container.run(["/usr/bin/id", "-u"])
                    self.assertEqual(res.stdout.decode(), f"{user.user_id}\n")
                    self.assertEqual(res.stderr, b"")

                    res = container.run_script("#!/bin/sh\n/usr/bin/id -u\n")
                    self.assertEqual(res.stdout.decode(), f"{user.user_id}\n")
                    self.assertEqual(res.stderr, b"")

                    res = container.run(["/usr/bin/pwd"])
                    self.assertEqual(res.stdout.decode(), f"/tmp/{os.path.basename(workdir)}\n")
                    self.assertEqual(res.stderr, b"")

    def test_run_callable_logging(self):
        def test_log():
            logging.debug("debug")
            logging.info("info")
            logging.warning("warning")
            logging.error("error")

        self.maxDiff = None

        with privs.root():
            with self.system.create_container() as container:
                with self.assertLogs() as lg:
                    res = container.run_callable(test_log)
                self.assertEqual(res.stdout, b"")
                self.assertEqual(res.stderr, b"")

                logname = self.system.log.name
                self.assertEqual(lg.output, [
                    f"INFO:{logname}:Running test_log",
                    f"DEBUG:{logname}:debug",
                    f"INFO:{logname}:info",
                    f"WARNING:{logname}:warning",
                    f"ERROR:{logname}:error"])


# Create an instance of RunTestCase for each distribution in TEST_CHROOTS.
# The test cases will be named Test$DISTRO. For example:
#   TestCentos7, TestCentos8, TestFedora32, TestFedora34
this_module = sys.modules[__name__]
for distro_name in TEST_CHROOTS:
    cls_name = "Test" + distro_name.capitalize()
    test_case = type(cls_name, (RunTestCase, unittest.TestCase), {"distro_name": distro_name})
    test_case.__module__ = __name__
    setattr(this_module, cls_name, test_case)
