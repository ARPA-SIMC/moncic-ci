import abc
import subprocess

from moncic.operations import build as ops_build
from moncic.source import Source
from moncic.source.distro import DistroSource

from .base import (
    IntegrationTestsBase,
    NspawnIntegrationTestsBase,
    PodmanIntegrationTestsBase,
    setup_distro_tests,
    skip_if_container_cannot_start,
)


class BuildTests(IntegrationTestsBase, abc.ABC):
    @skip_if_container_cannot_start()
    def test_build(self) -> None:
        package = self.get_package("hello")
        workdir = self.workdir()
        source_path = workdir / "hello"
        package.as_git(source_path)

        rimage = self.get_bootstrapped()

        from moncic import context

        context.debug.set(True)

        with self.verbose_logging(), Source.create_local(source=source_path) as local_source:
            source = DistroSource.create_from_local(local_source, distro=rimage.distro)
            # Create a Build object with system-configured defaults
            builder_class = ops_build.Builder.get_builder_class(source)
            # Fill in the build configuration
            config = builder_class.build_config_class()
            builder = builder_class(source, rimage, config)
            builder.host_main()

            # TODO: verify build results
            self.assertTrue(builder.results.success)


class NspawnBuildTests(BuildTests, NspawnIntegrationTestsBase, abc.ABC):
    pass


class PodmanBuildTests(BuildTests, PodmanIntegrationTestsBase, abc.ABC):
    pass


class FedoraBuildTests(BuildTests, abc.ABC):
    @skip_if_container_cannot_start()
    def test_build_fedora_sources(self) -> None:
        package = self.get_package("hello")
        workdir = self.workdir()
        source_path = workdir / "hello"
        package.as_git(source_path)
        fedora_sources_path = source_path / "fedora" / "SOURCES"
        fedora_sources_path.mkdir()
        (fedora_sources_path / "testfile").write_text("test file")
        subprocess.run(["git", "add", "fedora/SOURCES"], cwd=source_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Added a test file to SOURCES"], cwd=source_path, capture_output=True, check=True
        )

        rimage = self.get_bootstrapped()

        from moncic import context

        context.debug.set(True)

        with self.verbose_logging(), Source.create_local(source=source_path) as local_source:
            source = DistroSource.create_from_local(local_source, distro=rimage.distro)
            # Create a Build object with system-configured defaults
            builder_class = ops_build.Builder.get_builder_class(source)
            # Fill in the build configuration
            config = builder_class.build_config_class()
            builder = builder_class(source, rimage, config)
            builder.host_main()

            # TODO: verify build results
            self.assertTrue(builder.results.success)


bases: dict[str, type[IntegrationTestsBase]] = {
    "nspawn": NspawnBuildTests,
    "podman": PodmanBuildTests,
    "family:fedora": FedoraBuildTests,
}


setup_distro_tests(__name__, bases, "BuildTests")

del FedoraBuildTests
del NspawnBuildTests
del PodmanBuildTests
del BuildTests
