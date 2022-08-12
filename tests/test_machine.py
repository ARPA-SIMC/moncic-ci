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
from typing import Generator, Optional

from moncic.system import System
from moncic.container import BindConfig, Container, ContainerConfig, RunConfig, UserConfig
from moncic.unittest import TEST_CHROOTS, make_moncic, privs


class RunTestCase:
    distro_name: str

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cls_exit_stack = contextlib.ExitStack()
        cls.moncic = make_moncic()
        cls.images = cls.cls_exit_stack.enter_context(cls.moncic.images())

    @classmethod
    def tearDownClass(cls):
        cls.cls_exit_stack.close()
        cls.images = None
        cls.moncic = None
        cls.cls_exit_stack = None
        super().tearDownClass()

    @contextlib.contextmanager
    def system(self) -> Generator[System, None, None]:
        with privs.root():
            if self.distro_name not in self.images.list_images():
                raise unittest.SkipTest(f"Image {self.distro_name} not available")
            with self.images.system(self.distro_name) as system:
                if not os.path.exists(system.path):
                    raise unittest.SkipTest(f"Image {self.distro_name} has not been bootstrapped")
                yield system

    @contextlib.contextmanager
    def container(self, config: Optional[ContainerConfig] = None) -> Generator[Container, None, None]:
        with self.system() as system:
            with system.create_container(config=config) as container:
                yield container

    def test_true(self):
        with self.container() as container:
            container.run(["/usr/bin/true"])

    def test_sleep(self):
        with self.container() as container:
            start = time.time()
            container.run(["/usr/bin/sleep", "0.1"])
            # Check that 0.1 seconds have passed
            self.assertGreaterEqual(time.time() - start, 0.1)

    def test_stdout(self):
        with self.container() as container:
            res = container.run(["/usr/bin/echo", "test"])
            self.assertEqual(res.stdout, b"test\n")
            self.assertEqual(res.stderr, b"")

    def test_env(self):
        with self.container() as container:
            res = container.run(["/bin/sh", "-c", "echo $HOME"])
            self.assertEqual(res.stdout, b"/root\n")
            self.assertEqual(res.stderr, b"")

    def test_callable(self):
        token = secrets.token_bytes(8)
        with self.container() as container:
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
        with self.container() as container:
            def test_function():
                print("stdout")
                print("stderr", file=sys.stderr)

            res = container.run_callable(test_function)
            self.assertEqual(res.stdout, b'stdout\n')
            self.assertEqual(res.stderr, b'stderr\n')
            self.assertEqual(res.returncode, 0)

    def test_multi_maint_runs(self):
        with self.system() as system:
            with system.create_container() as container:
                res = container.run(["/bin/echo", "1"])
                self.assertEqual(res.stdout, b"1\n")
                self.assertEqual(res.stderr, b"")
            with system.create_container() as container:
                res = container.run(["/bin/echo", "2"])
                self.assertEqual(res.stdout, b"2\n")
                self.assertEqual(res.stderr, b"")

    def test_run_script(self):
        with self.container() as container:
            res = container.run_script("#!/bin/sh\nA=test\necho $A\nexit 1\n", config=RunConfig(check=False))
            self.assertEqual(res.stdout, b"test\n")
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 1)

    def test_forward_user(self):
        def get_user():
            print(json.dumps(UserConfig.from_current()))

        user = UserConfig.from_sudoer()

        # By default, things are run as root
        with self.system() as system:
            container_config = ContainerConfig()
            with system.create_container(config=container_config) as container:
                res = container.run_callable(get_user)
                u = UserConfig(*json.loads(res.stdout))
                self.assertEqual(res.stderr, b"")
                self.assertEqual(u, UserConfig("root", 0, "root", 0))

                # Running with another user fails as it does not exist in the
                # container
                with self.assertRaises(subprocess.CalledProcessError) as e:
                    res = container.run_callable(get_user, config=RunConfig(user=user))
                self.assertRegex(e.exception.stderr.decode(), "RuntimeError: container has no user 1000 'enrico'")

            container_config = ContainerConfig(forward_user=user)
            with system.create_container(config=container_config) as container:
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
            container_config = ContainerConfig()
            container_config.configure_workdir(workdir)
            with self.container(config=container_config) as container:
                res = container.run(["/usr/bin/id", "-u"])
                self.assertEqual(res.stdout.decode(), f"{user.user_id}\n")
                self.assertEqual(res.stderr, b"")

                res = container.run_script("#!/bin/sh\n/usr/bin/id -u\n")
                self.assertEqual(res.stdout.decode(), f"{user.user_id}\n")
                self.assertEqual(res.stderr, b"")

                res = container.run(["/usr/bin/pwd"])
                self.assertEqual(res.stdout.decode(), f"/media/{os.path.basename(workdir)}\n")
                self.assertEqual(res.stderr, b"")

                binds = list(container.binds())
                self.assertEqual(len(binds), 1)
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/" + os.path.basename(workdir))
                self.assertEqual(binds[0].bind_type, "rw")
                self.assertEqual(binds[0].mount_options, [])

    def test_bind_mount_rw(self):
        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig()
            container_config.binds.append(BindConfig(source=workdir, destination="/media/workdir", bind_type="rw"))
            with self.container(config=container_config) as container:
                container.run(["/bin/touch", "/media/workdir/test"])
                container.run(["/bin/test", "-e", "/media/workdir/test"])
                self.assertTrue(os.path.exists(os.path.join(workdir, "test")))

                binds = list(container.binds())
                self.assertEqual(len(binds), 1)
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/workdir")
                self.assertEqual(binds[0].bind_type, "rw")
                self.assertEqual(binds[0].mount_options, [])

    def test_bind_mount_ro(self):
        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig()
            container_config.binds.append(BindConfig(source=workdir, destination="/media/workdir", bind_type="ro"))
            with self.container(config=container_config) as container:
                res = container.run(["/bin/touch", "/media/workdir/test"], config=RunConfig(check=False))
                self.assertEqual(res.returncode, 1)

                container.run(["/bin/test", "!", "-e", "/media/workdir/test"])
                self.assertFalse(os.path.exists(os.path.join(workdir, "test")))

                binds = list(container.binds())
                self.assertEqual(len(binds), 1)
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/workdir")
                self.assertEqual(binds[0].bind_type, "ro")
                self.assertEqual(binds[0].mount_options, [])

    def test_bind_mount_volatile(self):
        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig()
            container_config.binds.append(
                    BindConfig(source=workdir, destination="/media/workdir", bind_type="volatile"))
            with self.container(config=container_config) as container:
                container.run(["/bin/touch", "/media/workdir/test"])
                container.run(["/bin/test", "-e", "/media/workdir/test"])
                self.assertFalse(os.path.exists(os.path.join(workdir, "test")))

                binds = list(container.binds())
                self.assertEqual(len(binds), 1)
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/workdir")
                self.assertEqual(binds[0].bind_type, "volatile")
                self.assertEqual(binds[0].mount_options, [])

    def test_run_callable_logging(self):
        def test_log():
            logging.debug("debug")
            logging.info("info")
            logging.warning("warning")
            logging.error("error")

        self.maxDiff = None

        with self.container() as container:
            with self.assertLogs() as lg:
                res = container.run_callable(test_log)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")

            logname = container.system.log.name
            self.assertEqual(lg.output, [
                f"INFO:{logname}:Running test_log",
                f"DEBUG:{logname}:debug",
                f"INFO:{logname}:info",
                f"WARNING:{logname}:warning",
                f"ERROR:{logname}:error"])

    def test_issue37(self):
        def test_redirect():
            sys.stdin.read(1)

        self.maxDiff = None

        with self.container() as container:
            res = container.run_callable(test_redirect)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")


# Create an instance of RunTestCase for each distribution in TEST_CHROOTS.
# The test cases will be named Test$DISTRO. For example:
#   TestCentos7, TestCentos8, TestFedora32, TestFedora34
this_module = sys.modules[__name__]
for distro_name in TEST_CHROOTS:
    cls_name = "Test" + distro_name.capitalize()
    test_case = type(cls_name, (RunTestCase, unittest.TestCase), {"distro_name": distro_name})
    test_case.__module__ = __name__
    setattr(this_module, cls_name, test_case)
