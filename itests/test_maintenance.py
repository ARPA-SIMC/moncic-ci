import abc
import contextlib
import logging
import shutil
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import ClassVar, override

from rich.logging import RichHandler

from moncic import context
from moncic.distro import Distro, DistroFamily
from moncic.image import RunnableImage
from moncic.images import BootstrappingImages
from moncic.moncic import Moncic, MoncicConfig
from moncic.nspawn.images import BtrfsImages, NspawnImages, PlainImages
from moncic.podman.images import PodmanImages
from moncic.provision.images import DistroImages
from moncic.session import Session
from moncic.unittest import MoncicTestCase
from moncic.utils.btrfs import is_btrfs


class DistroMaintenanceTests(MoncicTestCase, abc.ABC):
    distro: ClassVar[Distro]
    session: ClassVar[Session]
    distro_images: ClassVar[DistroImages]
    images: ClassVar[BootstrappingImages]
    bootstrapped: ClassVar[RunnableImage | None]

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
            with cls.verbose_logging():
                bimage = cls.distro_images.image(cls.distro.name)
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

    def test_bootstrap(self) -> None:
        self.get_bootstrapped()

    def test_update(self) -> None:
        rimage = self.get_bootstrapped()
        rimage.update()

    # def test_remove(self) -> None:
    #     raise NotImplementedError()

    # def test_build(self) -> None:
    #     raise NotImplementedError()


class NspawnDistroMaintenanceTests(DistroMaintenanceTests, abc.ABC):
    @override
    @classmethod
    def tearDownClass(cls) -> None:
        with context.privs.root():
            assert isinstance(cls.images, NspawnImages)
            shutil.rmtree(cls.images.imagedir)
        super().tearDownClass()

    @override
    @classmethod
    def make_images(cls) -> NspawnImages:
        image_path = Path(cls.enterClassContext(tempfile.TemporaryDirectory(delete=False)))
        if is_btrfs(image_path):
            return BtrfsImages(cls.session, image_path)
        else:
            return PlainImages(cls.session, image_path)


class PodmanDistroMaintenanceTests(DistroMaintenanceTests, abc.ABC):
    @override
    @classmethod
    def make_session(cls) -> Session:
        session = super().make_session()
        session.podman_repository = "localhost/moncic-ci-tests"
        return session

    @override
    @classmethod
    def make_images(cls) -> PodmanImages:
        return PodmanImages(cls.session)

    def test_get_podman_name(self) -> None:
        repo, tag = self.distro.get_podman_name()
        name = f"{repo}:{tag}"
        with self.subTest(name=name):
            self.session.podman.images.pull(repo, tag)
            self.assertTrue(self.session.podman.images.exists(name))


bases: dict[str, type[DistroMaintenanceTests]] = {
    "nspawn": NspawnDistroMaintenanceTests,
    "podman": PodmanDistroMaintenanceTests,
}


def setup_tests() -> None:

    # Create an instance of DistroMaintenanceTests for each distribution in TEST_CHROOTS.
    # The test cases will be named Test$DISTRO. For example:
    #   TestCentos7, TestCentos8, TestFedora32, TestFedora34
    this_module = sys.modules[__name__]
    for distro_family in DistroFamily.list_families():
        for distro_name in sorted({di.name for di in distro_family.list_distros()}):
            distro = DistroFamily.lookup_distro(distro_name)
            for tech in "nspawn", "podman":
                base = bases[tech]
                name = "".join(n.capitalize() for n in distro.name.split(":"))
                cls_name = name + tech.capitalize() + "DistroMaintenanceTests"
                test_case = type(cls_name, (base,), {"distro": distro})
                test_case.__module__ = __name__
                setattr(this_module, cls_name, test_case)


setup_tests()

del NspawnDistroMaintenanceTests
del PodmanDistroMaintenanceTests
del DistroMaintenanceTests
