import contextlib
import logging
import os
import secrets
import sys
import tempfile
import time
import unittest
from collections.abc import Generator
from typing import ClassVar
from pathlib import Path

from moncic import context
from moncic.container import BindConfig, Container, ContainerConfig
from moncic.nspawn.images import PlainImages
from moncic.nspawn.image import NspawnImage
from moncic.runner import UserConfig
from moncic.unittest import MoncicTestCase


class TestNspawnContainer(MoncicTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.imageconfdir = self.workdir()
        self.mconfig = self.config()
        self.mconfig.imageconfdirs.append(self.imageconfdir)
        assert self.mconfig.imagedir is not None
        self.imagedir: Path = self.mconfig.imagedir
        self.image_yaml = self.imageconfdir / "test.yaml"
        self.image_yaml.write_text("distro: fedora34\n")
        self.session = self.enterContext(self.mock_session(self.moncic(self.mconfig), images_class=PlainImages))
        self.images = self.session.images.images[-1]

    @contextlib.contextmanager
    def image(self) -> Generator[NspawnImage, None, None]:
        with context.privs.root():
            images = self.images.list_images()
            if not images:
                raise unittest.SkipTest("No nspawn test images are available")
            image = self.images.image(images[0])
            assert isinstance(image, NspawnImage)
            yield image

    @contextlib.contextmanager
    def container(self, config: ContainerConfig | None = None) -> Generator[Container, None, None]:
        with self.image() as image:
            with image.container(config=config) as container:
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

            res = container.run_callable_raw(test_function)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            self.assertIsNone(res.returnvalue)
            self.assertIsNone(res.exc_info)

            res = container.run(["/usr/bin/cat", "/tmp/token"])
            self.assertEqual(res.stdout, token)
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            self.assertIsNone(res.returnvalue)
            self.assertIsNone(res.exc_info)

    def test_callable_prints(self):
        with self.container() as container:

            def test_function():
                print("stdout")
                print("stderr", file=sys.stderr)

            res = container.run_callable_raw(test_function)
            self.assertEqual(res.stdout, b"stdout\n")
            self.assertEqual(res.stderr, b"stderr\n")
            self.assertEqual(res.returncode, 0)
            self.assertIsNone(res.returnvalue)
            self.assertIsNone(res.exc_info)

    def test_callable_returns(self):
        with self.container() as container:

            def test_function():
                return {"success": True}

            res = container.run_callable_raw(test_function)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.returnvalue, {"success": True})
            self.assertIsNone(res.exc_info)
            self.assertEqual(res.result(), {"success": True})

    def test_callable_raises(self):
        with self.container() as container:

            def test_function():
                raise RuntimeError("expected failure")

            res = container.run_callable_raw(test_function)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            self.assertIsNone(res.returnvalue)
            self.assertIsNotNone(res.exc_info)
            self.assertEqual(res.exc_info[0], RuntimeError)
            self.assertIsInstance(res.exc_info[1], RuntimeError)
            self.assertEqual(str(res.exc_info[1]), "expected failure")
            with self.assertRaises(RuntimeError):
                res.result()

    def test_multi_maint_runs(self):
        with self.image() as image:
            with image.container() as container:
                res = container.run(["/bin/echo", "1"])
                self.assertEqual(res.stdout, b"1\n")
                self.assertEqual(res.stderr, b"")
            with image.container() as container:
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
            return UserConfig.from_current()

        user = UserConfig.from_sudoer()

        # By default, things are run as root
        with self.image() as image:
            container_config = ContainerConfig()
            with image.container(config=container_config) as container:
                u = container.run_callable(get_user)
                self.assertEqual(u, UserConfig("root", 0, "root", 0))

                # Running with another user fails as it does not exist in the
                # container
                with self.assertRaises(RuntimeError) as e:
                    container.run_callable(get_user, config=RunConfig(user=user)).result()
                self.assertEqual(str(e.exception), "container has no user 1000 'enrico'")

            container_config = ContainerConfig(forward_user=user)
            with image.container(config=container_config) as container:
                u = container.run_callable(get_user)
                self.assertEqual(u, UserConfig("root", 0, "root", 0))

                u = container.run_callable_raw(get_user, config=RunConfig(user=user)).result()
                self.assertEqual(u, user)

                res = container.run_script("#!/bin/sh\n/bin/true\n", config=RunConfig(user=user))
                self.assertEqual(res.stdout, b"")
                self.assertEqual(res.stderr, b"")

    def test_forward_user_workdir(self):
        user = UserConfig.from_sudoer()
        workdir = self.tempdir()

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
            self.assertIn(len(binds), (1, 2))
            self.assertEqual(binds[0].source, workdir)
            self.assertEqual(binds[0].destination, "/media/" + os.path.basename(workdir))
            self.assertEqual(binds[0].bind_type, "rw")
            self.assertEqual(binds[0].mount_options, [])
            if len(binds) == 2:
                # self.assertEqual(binds[1].source, workdir)
                self.assertEqual(binds[1].destination, "/var/cache/apt/archives")
                self.assertEqual(binds[1].bind_type, "rw")
                self.assertEqual(binds[1].mount_options, [])

    def test_bind_mount_rw(self):
        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig()
            container_config.binds.append(
                BindConfig.create(source=workdir, destination="/media/workdir", bind_type="rw")
            )
            with self.container(config=container_config) as container:
                container.run(["/bin/touch", "/media/workdir/test"])
                container.run(["/bin/test", "-e", "/media/workdir/test"])
                self.assertTrue(os.path.exists(os.path.join(workdir, "test")))

                binds = list(container.binds())
                self.assertIn(len(binds), (1, 2))
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/workdir")
                self.assertEqual(binds[0].bind_type, "rw")
                self.assertEqual(binds[0].mount_options, [])
                if len(binds) == 2:
                    # self.assertEqual(binds[1].source, workdir)
                    self.assertEqual(binds[1].destination, "/var/cache/apt/archives")
                    self.assertEqual(binds[1].bind_type, "rw")
                    self.assertEqual(binds[1].mount_options, [])

    def test_bind_mount_ro(self):
        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig()
            container_config.binds.append(
                BindConfig.create(source=workdir, destination="/media/workdir", bind_type="ro")
            )
            with self.container(config=container_config) as container:
                res = container.run(["/bin/touch", "/media/workdir/test"], config=RunConfig(check=False))
                self.assertEqual(res.returncode, 1)

                container.run(["/bin/test", "!", "-e", "/media/workdir/test"])
                self.assertFalse(os.path.exists(os.path.join(workdir, "test")))

                binds = list(container.binds())
                self.assertIn(len(binds), (1, 2))
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/workdir")
                self.assertEqual(binds[0].bind_type, "ro")
                self.assertEqual(binds[0].mount_options, [])
                if len(binds) == 2:
                    # self.assertEqual(binds[1].source, workdir)
                    self.assertEqual(binds[1].destination, "/var/cache/apt/archives")
                    self.assertEqual(binds[1].bind_type, "rw")
                    self.assertEqual(binds[1].mount_options, [])

    def test_bind_mount_volatile(self):
        with tempfile.TemporaryDirectory() as workdir:
            # By default, things are run as root
            container_config = ContainerConfig()
            container_config.binds.append(
                BindConfig.create(source=workdir, destination="/media/workdir", bind_type="volatile")
            )
            with self.container(config=container_config) as container:
                container.run(["/bin/touch", "/media/workdir/test"])
                container.run(["/bin/test", "-e", "/media/workdir/test"])
                self.assertFalse(os.path.exists(os.path.join(workdir, "test")))

                binds = list(container.binds())
                self.assertIn(len(binds), (1, 2))
                self.assertEqual(binds[0].source, workdir)
                self.assertEqual(binds[0].destination, "/media/workdir")
                self.assertEqual(binds[0].bind_type, "ro")
                self.assertEqual(binds[0].mount_options, [])
                if len(binds) == 2:
                    # self.assertEqual(binds[1].source, workdir)
                    self.assertEqual(binds[1].destination, "/var/cache/apt/archives")
                    self.assertEqual(binds[1].bind_type, "rw")
                    self.assertEqual(binds[1].mount_options, [])

    def test_run_callable_logging(self):
        def test_log():
            logging.debug("debug")
            logging.info("info")
            logging.warning("warning")
            logging.error("error")

        self.maxDiff = None

        with self.container() as container:
            with self.assertLogs(level=logging.DEBUG) as lg:
                res = container.run_callable_raw(test_log)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            self.assertIsNone(res.returnvalue)
            self.assertIsNone(res.exc_info)
            self.assertIsNone(res.result())

            output = [line for line in lg.output if "asyncio" not in line]
            self.assertEqual(
                output,
                [
                    f"INFO:{container.system.log.name}:Running test_log",
                    f"DEBUG:{container.system.log.name}.root:debug",
                    f"INFO:{container.system.log.name}.root:info",
                    f"WARNING:{container.system.log.name}.root:warning",
                    f"ERROR:{container.system.log.name}.root:error",
                ],
            )

    def test_issue37(self):
        def test_redirect():
            sys.stdin.read(1)

        self.maxDiff = None

        with self.container() as container:
            res = container.run_callable_raw(test_redirect)
            self.assertEqual(res.stdout, b"")
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            self.assertIsNone(res.returnvalue)
            self.assertIsNone(res.exc_info)
            self.assertIsNone(res.result())
