import io
import tarfile
from pathlib import Path
from typing import override
from unittest import mock

from moncic import context
from moncic.image import BootstrappableImage, RunnableImage
from moncic.nspawn.image import NspawnImage
from moncic.nspawn.images import BtrfsImages, NspawnImages, PlainImages
from moncic.unittest import MoncicTestCase


class NspawnImagesTests(MoncicTestCase):
    images_class: type[NspawnImages]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.imageconfdir = self.workdir()
        self.mconfig = self.config()
        self.mconfig.imageconfdirs.append(self.imageconfdir)
        assert self.mconfig.imagedir is not None
        self.imagedir: Path = self.mconfig.imagedir
        self.image_yaml = self.imageconfdir / "test.yaml"
        self.image_yaml.write_text("distro: fedora34\n")
        self.session = self.enterContext(self.mock_session(self.moncic(self.mconfig), images_class=self.images_class))
        self.images = self.session.images.images[-1]

    def mock_bootstrap(self, path: Path) -> None:
        with context.privs.root():
            path.mkdir(exist_ok=True)
            os_release = path / "etc" / "os-release"
            os_release.parent.mkdir(parents=True)
            os_release.write_text(
                """
ID=fedora
VERSION_ID=34
"""
            )

    def test_query(self) -> None:
        # Images that are not bootstrapped do not show up
        self.assertFalse(self.images.has_image("test"))
        self.assertEqual(self.images.list_images(), [])
        with self.assertRaisesRegex(KeyError, "Image 'test' not found"):
            self.images.image("test")

        # Simulate the image being bootstrapped
        self.mock_bootstrap(self.imagedir / "test")

        # Now it shows up
        self.assertTrue(self.images.has_image("test"))
        self.assertEqual(self.images.list_images(), ["test"])
        self.assertIsInstance(self.images.image("test"), RunnableImage)

    def test_bootstrap(self) -> None:
        self.session.images.reload()
        image = self.session.images.image("test")
        assert isinstance(image, BootstrappableImage)
        image_path = self.imagedir / "test"
        self.assertIsInstance(image, BootstrappableImage)

        with mock.patch(
            "moncic.distro.rpm.FedoraDistro.bootstrap", side_effect=lambda images, path: self.mock_bootstrap(path)
        ) as distro_bootstrap:
            bootstrapped = image.bootstrap()
        distro_bootstrap.assert_called_with(self.images, Path(f"{image_path}.new"))
        self.assertIsInstance(bootstrapped, NspawnImage)
        self.assertEqual(bootstrapped.path, image_path)

    def test_bootstrap_tarball(self) -> None:
        self.session.images.reload()
        image = self.session.images.image("test")
        assert isinstance(image, BootstrappableImage)
        image_path = self.imagedir / "test"
        self.assertIsInstance(image, BootstrappableImage)

        # Create a mock tarball for fedora34
        tar_path = self.imagedir / "fedora34.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            contents = b"ID=fedora\nVERSION_ID=34\n"
            tarinfo = tarfile.TarInfo("etc/os-release")
            tarinfo.size = len(contents)
            with io.BytesIO(contents) as fd:
                tar.addfile(tarinfo, fd)

        bootstrapped = image.bootstrap()
        self.assertIsInstance(bootstrapped, NspawnImage)
        self.assertEqual(bootstrapped.path, image_path)

        self.assertTrue((self.imagedir / "test" / "etc" / "os-release").exists())


class BtrfsNspawnImagesTests(NspawnImagesTests):
    images_class: type[NspawnImages] = BtrfsImages
    DEFAULT_FILESYSTEM_TYPE = "btrfs"


class PlainNspawnImagesTests(NspawnImagesTests):
    images_class: type[NspawnImages] = PlainImages
    DEFAULT_FILESYSTEM_TYPE = "tmpfs"


del NspawnImagesTests
