from typing import override

from moncic.distro import Distro, DistroFamily
from moncic.moncic import Moncic, MoncicConfig
from moncic.provision.image import DistroImage
from moncic.provision.images import DistroImages
from moncic.unittest import MockMoncicTestCase


class DistroImagesTests(MockMoncicTestCase):
    @override
    def make_moncic_config(self) -> MoncicConfig:
        config = super().make_moncic_config()
        config.auto_sudo = False
        return config

    @override
    def setUp(self) -> None:
        super().setUp()
        self.session = self.enterContext(self.moncic.session())
        self.images = DistroImages(self.session)

    def all_distros(self) -> list[Distro]:
        res: list[Distro] = []
        for family in DistroFamily.list_families():
            for distro in family.distros:
                res.append(distro)
        return res

    def test_has_image(self) -> None:
        for distro in self.all_distros():
            names = [distro.full_name]
            names.extend(distro.aliases)
            for name in names:
                with self.subTest(distro=name):
                    self.assertTrue(self.images.has_image(name))

    def test_has_image_nonexistent(self) -> None:
        self.assertFalse(self.images.has_image("does-not-exist"))

    def test_list_images(self) -> None:
        self.assertCountEqual(self.images.list_images(), [d.full_name for d in self.all_distros()])

    def test_image(self) -> None:
        for distro in self.all_distros():
            names = [distro.full_name]
            names.extend(distro.aliases)
            for name in names:
                with self.subTest(distro=name):
                    image = self.images.image(name)
                    assert isinstance(image, DistroImage)
                    self.assertEqual(image.distro, distro)
