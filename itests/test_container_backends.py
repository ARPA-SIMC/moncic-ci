import abc
import io
import json
from pathlib import Path
from subprocess import CalledProcessError

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

    @skip_if_container_cannot_start()
    def test_run(self) -> None:
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            res = container.run(["/bin/cat", "/etc/os-release"])
        with io.StringIO(res.stdout.decode()) as fd:
            osr = parse_osrelase_contents(fd, "/etc/os-release")
        distro = DistroFamily.from_osrelease(osr, "test")
        # Testing and sid (and sometimes the upcoming stable) are
        # indistinguishable from os-release contents
        if self.distro.full_name in ("debian:testing", "debian:sid"):
            self.assertIs(distro.family, self.distro.family)
        else:
            self.assertIs(distro, self.distro)

    @skip_if_container_cannot_start()
    def test_run_cwd(self) -> None:
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            res = container.run(["/usr/bin/pwd"], config=RunConfig(cwd=Path("/tmp")))
        self.assertEqual(res.stdout.decode().strip(), "/tmp")
        self.assertEqual(res.stderr, b"")

    @skip_if_container_cannot_start()
    def test_run_user(self) -> None:
        rimage = self.get_bootstrapped()
        sudoer = UserConfig.from_sudoer()
        config = ContainerConfig()
        config.forward_user = sudoer
        with rimage.container(config=config) as container:
            res = container.run(["/usr/bin/id", "-u"], config=RunConfig(user=UserConfig.root(), cwd=Path("/")))
            self.assertEqual(res.stdout.decode().strip(), "0")
            self.assertEqual(res.stderr, b"")

            res = container.run(["/usr/bin/id", "-u"], config=RunConfig(user=sudoer, cwd=Path("/")))
            self.assertEqual(res.stdout.decode().strip(), str(sudoer.user_id))
            self.assertEqual(res.stderr, b"")

    @skip_if_container_cannot_start()
    def test_run_result(self) -> None:
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            res = container.run(["/usr/bin/false"], config=RunConfig(check=False))
        self.assertEqual(res.stdout, b"")
        self.assertEqual(res.stderr, b"")
        self.assertEqual(res.returncode, 1)

    @skip_if_container_cannot_start()
    def test_run_check(self) -> None:
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            with self.assertRaisesRegex(CalledProcessError, r"returned non-zero exit status 1."):
                container.run(["/usr/bin/false"], config=RunConfig(check=True))

    @skip_if_container_cannot_start()
    def test_run_disable_network(self) -> None:
        self.skipTest("TODO: find a way to implement this in podman and nspawn once the container has started")
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            res = container.run(["/usr/sbin/ip", "-json", "link", "show"], config=RunConfig(disable_network=True))
            self.assertEqual(res.stderr, b"")
            self.assertEqual(res.returncode, 0)
            parsed = json.loads(res.stdout)
            self.assertEqual([x["ifname"] for x in parsed], ["lo"])

    @skip_if_container_cannot_start()
    def test_run_script_path(self) -> None:
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            script = Script("Check path")
            script.run_unquoted("echo $PATH")
            res = container.run_script(script)
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


bases: dict[str, type[IntegrationTestsBase]] = {
    "nspawn": NspawnDistroMaintenanceTests,
    "podman": PodmanDistroMaintenanceTests,
}


setup_distro_tests(__name__, bases, "DistroMaintenanceTests")

del NspawnDistroMaintenanceTests
del PodmanDistroMaintenanceTests
del DistroMaintenanceTests
