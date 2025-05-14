import contextlib
import logging
import os
from pathlib import Path
from typing import Generator, override

from moncic.nspawn.images import NspawnImages
from moncic.nspawn.system import MaintenanceSystem

from .system import MockMaintenanceSystem, MockSystem
from .image import MockImage

log = logging.getLogger("images")


class MockImages(NspawnImages):
    """
    Mock image storage, used for testing
    """

    @override
    def image(self, name: str) -> MockImage:
        image = MockImage(images=self, name=name, path=Path("/tmp/mock-moncic-ci"))
        image.distro = name
        return image

    @contextlib.contextmanager
    def system(self, name: str) -> Generator[MockSystem, None, None]:
        image = self.image(name)
        yield MockSystem(self, image)

    @contextlib.contextmanager
    def maintenance_system(self, name: str) -> Generator[MaintenanceSystem, None, None]:
        image = self.image(name)
        yield MockMaintenanceSystem(self, image)

    def remove_system(self, name: str):
        path = os.path.join(self.imagedir, name)
        self.session.mock_log(system=name, action="rmtree", arg=path)
