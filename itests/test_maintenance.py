import abc

from .base import IntegrationTestsBase, NspawnIntegrationTestsBase, PodmanIntegrationTestsBase, setup_distro_tests


class DistroMaintenanceTests(IntegrationTestsBase, abc.ABC):
    def test_bootstrap(self) -> None:
        self.get_bootstrapped()

    def test_update(self) -> None:
        rimage = self.get_bootstrapped()
        rimage.update()

    def test_run(self) -> None:
        # TODO: run cat /etc/os-release, use it to lookup a distro and verify it's the same as self.distro
        pass

    # def test_remove(self) -> None:
    #     TODO: make a pretend image for nspawn
    #     raise NotImplementedError()

    # On another integration test, making images persist across TestCases
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
