import abc
import contextlib
import logging
import shutil
import os
import subprocess
from unittest import SkipTest
from collections.abc import Generator
from pathlib import Path
from typing import ClassVar, override

from rich.logging import RichHandler

from moncic.container import ContainerCannotStart
from moncic.distro import Distro, DistroFamily
from moncic.image import RunnableImage
from moncic.images import BootstrappingImages
from moncic.moncic import Moncic, MoncicConfig
from moncic.nspawn.images import BtrfsImages, NspawnImages, PlainImages
from moncic.podman.images import PodmanImages
from moncic.provision.images import DistroImages
from moncic.session import Session
from moncic.unittest import MoncicTestCase, add_testcase
from moncic.utils.btrfs import is_btrfs


@contextlib.contextmanager
def skip_if_container_cannot_start() -> Generator[None, None, None]:
    try:
        yield None
    except ContainerCannotStart as exc:
        raise SkipTest(f"Container cannot start: {exc}")


class Package:
    """Test package from the integration test data."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.path = path

    def files(self) -> list[Path]:
        """Return the list of files (as relative paths) in this package."""
        res = subprocess.run(["git", "ls-files"], cwd=self.path.as_posix(), text=True, capture_output=True, check=True)
        return [Path(f) for f in res.stdout.splitlines()]

    def as_git(self, path: Path) -> None:
        """Build a git repository of this package in the given path."""
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
        for file in self.files():
            src = self.path / file
            dest = path / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, capture_output=True, check=True)

    # If we need a source tarball:
    # with tarfile.open("hello_1.0.orig.tar.gz", "w:gz") as tar:
    #     for file in files:
    #         tar.add(file)


class IntegrationTestsBase(MoncicTestCase, abc.ABC):
    """Base test case class for integration tests."""

    distro: ClassVar[Distro]
    session: ClassVar[Session]
    distro_images: ClassVar[DistroImages]
    images: ClassVar[BootstrappingImages]
    bootstrapped: ClassVar[RunnableImage | None]

    @classmethod
    def get_config(self, name: str) -> str:
        """Return an integration test configuration name passed by the ./test script."""
        if (value := os.environ.get(f"MONCIC_ITESTS_{name.upper()}", None)) is None:
            raise RuntimeError("integration tests need to be run using `./test -i`")
        return value

    @classmethod
    def get_package(cls, name: str = "hello") -> Package:
        """Return a Package object from the integration test data."""
        packages_dir = Path(cls.get_config("top_srcdir")) / "itests"
        return Package(packages_dir / name)

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.session = cls.make_session()
        cls.distro_images = DistroImages(cls.session)
        cls.images = cls.make_images()
        cls.bootstrapped = None

    @classmethod
    def make_session(cls) -> Session:
        config = MoncicConfig()
        config.imageconfdirs = []
        config.auto_sudo = False
        config.deb_cache_dir = None
        return cls.enterClassContext(Moncic(config).session())

    @classmethod
    @abc.abstractmethod
    def make_images(cls) -> BootstrappingImages: ...

    @classmethod
    def get_bootstrapped(cls) -> RunnableImage:
        if not cls.bootstrapped:
            image = cls.images.image(cls.distro.full_name)
            if isinstance(image, RunnableImage):
                cls.bootstrapped = image
                return image

            with cls.verbose_logging():
                # Make sure we bootstrap purely from the DistroImage
                bimage = cls.distro_images.image(cls.distro.full_name)
                cls.bootstrapped = cls.images.bootstrap(bimage)
        return cls.bootstrapped

    @classmethod
    @contextlib.contextmanager
    def verbose_logging(cls) -> Generator[None, None, None]:
        print()
        handler = RichHandler()
        handler.setLevel(logging.INFO)
        root_logger = logging.getLogger()
        orig_root_level = root_logger.level
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        try:
            yield
        finally:
            root_logger.setLevel(orig_root_level)
            root_logger.removeHandler(handler)


class NspawnIntegrationTestsBase(IntegrationTestsBase, abc.ABC):
    @override
    @classmethod
    def make_images(cls) -> NspawnImages:
        image_path = Path(cls.get_config("workdir"))
        image_path.mkdir(exist_ok=True, parents=True)
        if is_btrfs(image_path):
            return BtrfsImages(cls.session, image_path)
        else:
            return PlainImages(cls.session, image_path)


class PodmanIntegrationTestsBase(IntegrationTestsBase, abc.ABC):
    @override
    @classmethod
    def make_session(cls) -> Session:
        session = super().make_session()
        session.podman_repository = cls.get_config("podman_repo")
        return session

    @override
    @classmethod
    def make_images(cls) -> PodmanImages:
        return PodmanImages(cls.session)


def setup_distro_tests(module_name: str, bases: dict[str, type[IntegrationTestsBase]], suffix: str) -> None:
    """Generate one test per supported distribution and container technologies."""
    for distro_family in DistroFamily.list_families():
        for distro in distro_family.distros:
            for tech in "nspawn", "podman":
                base = bases[tech]
                name = "".join(n.capitalize() for n in distro.full_name.split(":"))
                cls_name = name + tech.capitalize() + suffix
                test_case = type(cls_name, (base,), {"distro": distro})
                add_testcase(module_name, test_case)
