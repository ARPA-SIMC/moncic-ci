import abc
import io

from moncic.distro import DistroFamily
from moncic.utils.osrelease import parse_osrelase_contents

from .base import IntegrationTestsBase, NspawnIntegrationTestsBase, PodmanIntegrationTestsBase, setup_distro_tests


class DistroMaintenanceTests(IntegrationTestsBase, abc.ABC):
    def test_bootstrap(self) -> None:
        self.get_bootstrapped()

    def test_update(self) -> None:
        rimage = self.get_bootstrapped()
        with self.verbose_logging():
            rimage.update()

    def test_run(self) -> None:
        rimage = self.get_bootstrapped()
        with rimage.container() as container:
            res = container.run(["/bin/cat", "/etc/os-release"])
        with io.StringIO(res.stdout.decode()) as fd:
            osr = parse_osrelase_contents(fd, "/etc/os-release")
        distro = DistroFamily.from_osrelease(osr, "test")
        self.assertEqual(distro, self.distro)

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
