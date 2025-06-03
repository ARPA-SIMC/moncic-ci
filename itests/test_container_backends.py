import abc
import io
import json
from pathlib import Path
from subprocess import CalledProcessError
from typing import override

from moncic.container import ContainerConfig
from moncic.distro import DistroFamily
from moncic.runner import RunConfig, UserConfig
from moncic.utils.osrelease import parse_osrelase_contents
from moncic.utils.script import Script

from .base import (
    IntegrationTestsBase,
    NspawnIntegrationTestsBase,
    PodmanIntegrationTestsBase,
    setup_distro_tests,
    skip_if_container_cannot_start,
)


class DistroMaintenanceTests(IntegrationTestsBase, abc.ABC):
    @skip_if_container_cannot_start()
    def test_bootstrap(self) -> None:
        self.get_bootstrapped()

    @skip_if_container_cannot_start()
    def test_update(self) -> None:
        rimage = self.get_bootstrapped()
        with self.verbose_logging():
            rimage.update()


class DistroContainerTests(IntegrationTestsBase, abc.ABC):
    @override
    @skip_if_container_cannot_start()
    def setUp(self) -> None:
        super().setUp()
        self.rimage = self.get_bootstrapped()
        self.sudoer = UserConfig.from_sudoer()
        config = ContainerConfig()
        config.forward_user = self.sudoer
        self.container = self.enterContext(self.rimage.container(config=config))

    def test_systemd_version(self) -> None:
        if (systemd_version := self.rimage.distro.systemd_version) is None:
            return
        res = self.container.run(["/bin/systemctl", "--version"])
        self.assertEqual(int(res.stdout.decode().splitlines()[0].split()[1]), systemd_version)
        self.assertEqual(res.stderr, b"")

    def test_run_osrelease(self) -> None:
        res = self.container.run(["/bin/cat", "/etc/os-release"])
        with io.StringIO(res.stdout.decode()) as fd:
            osr = parse_osrelase_contents(fd, "/etc/os-release")
        distro = DistroFamily.from_osrelease(osr, "test")
        # Testing and sid (and sometimes the upcoming stable) are
        # indistinguishable from os-release contents
        if self.distro.full_name in ("debian:testing", "debian:sid"):
            self.assertIs(distro.family, self.distro.family)
        else:
            self.assertIs(distro, self.distro)

    def test_run_cwd(self) -> None:
        res = self.container.run(["/bin/pwd"], config=RunConfig(cwd=Path("/tmp")))
        self.assertEqual(res.stdout.decode().strip(), "/tmp")
        self.assertEqual(res.stderr, b"")

    @skip_if_container_cannot_start()
    def test_run_no_absolute_path(self) -> None:
        res = self.container.run(["true"])
        self.assertEqual(res.stdout, b"")
        self.assertEqual(res.stderr, b"")

    @skip_if_container_cannot_start()
    def test_run_user(self) -> None:
        res = self.container.run(["/usr/bin/id", "-u"], config=RunConfig(user=UserConfig.root(), cwd=Path("/")))
        self.assertEqual(res.stdout.decode().strip(), "0")
        self.assertEqual(res.stderr, b"")

        res = self.container.run(["/usr/bin/id", "-u"], config=RunConfig(user=self.sudoer, cwd=Path("/")))
        self.assertEqual(res.stdout.decode().strip(), str(self.sudoer.user_id))
        self.assertEqual(res.stderr, b"")

    @skip_if_container_cannot_start()
    def test_run_result(self) -> None:
        res = self.container.run(["/bin/false"], config=RunConfig(check=False))
        self.assertEqual(res.stdout, b"")
        self.assertEqual(res.stderr, b"")
        self.assertEqual(res.returncode, 1)

    @skip_if_container_cannot_start()
    def test_run_check(self) -> None:
        with self.assertRaisesRegex(CalledProcessError, r"returned non-zero exit status 1."):
            self.container.run(["/bin/false"], config=RunConfig(check=True))

    @skip_if_container_cannot_start()
    def test_run_disable_network(self) -> None:
        self.skipTest("TODO: find a way to implement this in podman and nspawn once the container has started")
        res = self.container.run(["/usr/sbin/ip", "-json", "link", "show"], config=RunConfig(disable_network=True))
        self.assertEqual(res.stderr, b"")
        self.assertEqual(res.returncode, 0)
        parsed = json.loads(res.stdout)
        self.assertEqual([x["ifname"] for x in parsed], ["lo"])

    @skip_if_container_cannot_start()
    def test_run_script_path(self) -> None:
        script = Script("Check path")
        script.run_unquoted("echo $PATH")
        res = self.container.run_script(script)
        self.assertIn("/usr/bin", res.stdout.decode().strip())
        self.assertEqual(res.stderr, b"")
        self.assertEqual(res.returncode, 0)

    # def test_remove(self) -> None:
    #     TODO: make a pretend image for nspawn
    #     raise NotImplementedError()

    # Move to another set integration test
    # def test_build(self) -> None:
    #     raise NotImplementedError()


class NspawnDistroMaintenanceTests(DistroMaintenanceTests, NspawnIntegrationTestsBase, abc.ABC):
    pass


class PodmanDistroMaintenanceTests(DistroMaintenanceTests, PodmanIntegrationTestsBase, abc.ABC):
    def test_get_podman_name(self) -> None:
        repo, tag = self.distro.get_podman_name()
        name = f"{repo}:{tag}"
        with self.subTest(name=name):
            self.session.podman.images.pull(repo, tag)
            self.assertTrue(self.session.podman.images.exists(name))


class NspawnDistroContainerTests(DistroMaintenanceTests, NspawnIntegrationTestsBase, abc.ABC):
    pass


class PodmanDistroContainerTests(DistroMaintenanceTests, PodmanIntegrationTestsBase, abc.ABC):
    pass


setup_distro_tests(
    __name__,
    {
        "nspawn": NspawnDistroMaintenanceTests,
        "podman": PodmanDistroMaintenanceTests,
    },
    "ImageBackendTests",
)

setup_distro_tests(
    __name__,
    {
        "nspawn": NspawnDistroContainerTests,
        "podman": PodmanDistroContainerTests,
    },
    "ContainerBackendTests",
)


del NspawnDistroContainerTests
del PodmanDistroContainerTests
del DistroContainerTests
del NspawnDistroMaintenanceTests
del PodmanDistroMaintenanceTests
del DistroMaintenanceTests
