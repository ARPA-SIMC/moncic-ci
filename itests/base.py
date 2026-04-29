import abc
import contextlib
import logging
import os
import shutil
import subprocess
import time as tm
from collections.abc import Generator
from pathlib import Path
from typing import ClassVar, override
from unittest import SkipTest

import rich
import rich.text

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


class VerboseLogHandler(logging.Handler):
    """Log handler for integration test verbose logging."""

    def __init__(
        self, level: int | str = logging.NOTSET, *, test_id: str
    ) -> None:
        super().__init__(level)
        self.console = rich.get_console()
        self.test_id = test_id
        self.has_output: bool = False

    def format_level(self, record: logging.LogRecord) -> rich.text.Text:
        """Format the logging level."""
        # Taken from rich.logging.RichHandler.get_level_text
        level_name = record.levelname
        level_text = rich.text.Text.styled(
            level_name.ljust(8), f"logging.level.{level_name.lower()}"
        )
        return level_text

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if not self.has_output:
            self.console.rule(rich.markup.escape(f"🡇 {self.test_id} 🡇"))
        self.has_output = True

        message = rich.markup.escape(self.format(record))

        time = rich.text.Text.styled(
            tm.strftime("%H:%M:%S", tm.localtime(record.created)), "log.time"
        )
        fname = rich.text.Text.styled(
            rich.markup.escape(f"{record.filename}:{record.lineno}".ljust(25)),
            "log.path",
        )
        level = self.format_level(record)

        self.console.print(
            time,
            fname,
            level,
            message,
            highlight=False,
        )

    @override
    def close(self) -> None:
        if self.has_output:
            self.console.rule(rich.markup.escape(f"🡅 {self.test_id} 🡅"))
        super().close()


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
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=self.path.as_posix(),
            text=True,
            capture_output=True,
            check=True,
        )
        return [Path(f) for f in res.stdout.splitlines()]

    def as_git(self, path: Path) -> None:
        """Build a git repository of this package in the given path."""
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init"], cwd=path, capture_output=True, check=True
        )
        for file in self.files():
            src = self.path / file
            dest = path / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        subprocess.run(
            ["git", "add", "."], cwd=path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=path,
            capture_output=True,
            check=True,
        )

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
        if (
            value := os.environ.get(f"MONCIC_ITESTS_{name.upper()}", None)
        ) is None:
            raise RuntimeError(
                "integration tests need to be run using `./test -i`"
            )
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

    def get_bootstrapped(self) -> RunnableImage:
        if not self.bootstrapped:
            if self.images.has_image(self.distro.full_name):
                image = self.images.image(self.distro.full_name)
                assert isinstance(image, RunnableImage)
                self.__class__.bootstrapped = image
                return image

            with self.verbose_logging():
                # Make sure we bootstrap purely from the DistroImage
                bimage = self.distro_images.image(self.distro.full_name)
                self.__class__.bootstrapped = self.images.bootstrap(bimage)
        assert self.bootstrapped
        return self.bootstrapped

    @contextlib.contextmanager
    def verbose_logging(
        self, debug: bool = False
    ) -> Generator[None, None, None]:
        print()
        handler = VerboseLogHandler(test_id=self.id())
        level = logging.DEBUG if debug else logging.INFO
        handler.setLevel(level)
        root_logger = logging.getLogger()
        orig_root_level = root_logger.level
        root_logger.setLevel(level)
        root_logger.addHandler(handler)
        try:
            yield
        finally:
            root_logger.setLevel(orig_root_level)
            root_logger.removeHandler(handler)
            handler.close()


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


def setup_distro_tests(
    module_name: str, bases: dict[str, type[IntegrationTestsBase]], suffix: str
) -> None:
    """Generate one test per supported distribution and container technologies."""
    for distro_family in DistroFamily.list_families():
        for distro in distro_family.distros:
            for backend in "nspawn", "podman":
                parents: list[type[IntegrationTestsBase]] = []
                if base := bases.get(f"family:{distro_family.name}"):
                    parents.append(base)
                if base := bases.get(f"distro:{distro.name}"):
                    parents.append(base)
                parents.append(bases[backend])
                name = "".join(
                    n.capitalize() for n in distro.full_name.split(":")
                )
                cls_name = name + backend.capitalize() + suffix
                test_case = type(cls_name, tuple(parents), {"distro": distro})
                add_testcase(module_name, test_case)
