import abc

from moncic.operations import build as ops_build
from moncic.source import Source
from moncic.source.distro import DistroSource

from .base import (IntegrationTestsBase, NspawnIntegrationTestsBase,
                   PodmanIntegrationTestsBase, setup_distro_tests,
                   skip_if_container_cannot_start)


class BuildTests(IntegrationTestsBase, abc.ABC):
    @skip_if_container_cannot_start()
    def test_build(self) -> None:
        package = self.get_package("hello")
        workdir = self.workdir()
        source_path = workdir / "hello"
        package.as_git(source_path)

        rimage = self.get_bootstrapped()

        with self.verbose_logging(), Source.create_local(source=source_path) as local_source:
            source = DistroSource.create_from_local(local_source, distro=rimage.distro)
            # Create a Build object with system-configured defaults
            builder_class = ops_build.Builder.get_builder_class(source)
            # Fill in the build configuration
            config = builder_class.build_config_class()
            builder = builder_class(source, rimage, config)
            builder.host_main()

            # TODO: verify build results


class NspawnBuildTests(BuildTests, NspawnIntegrationTestsBase, abc.ABC):
    pass


class PodmanBuildTests(BuildTests, PodmanIntegrationTestsBase, abc.ABC):
    pass


bases: dict[str, type[IntegrationTestsBase]] = {
    "nspawn": NspawnBuildTests,
    "podman": PodmanBuildTests,
}


setup_distro_tests(__name__, bases, "BuildTests")

del NspawnBuildTests
del PodmanBuildTests
del BuildTests
