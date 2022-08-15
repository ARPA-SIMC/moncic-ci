from __future__ import annotations

import contextlib
from typing import Optional, TYPE_CHECKING

from . import imagestorage

if TYPE_CHECKING:
    from .moncic import Moncic


class Session(contextlib.ExitStack):
    """
    Hold shared resourcse during a single-threaded Moncic-CI work session
    """
    def __init__(self, moncic: Moncic):
        super().__init__()
        self.moncic = moncic

        # Storage for OS images
        self.image_storage: imagestorage.ImageStorage
        if self.moncic.config.imagedir is None:
            self.image_storage = imagestorage.ImageStorage.create_default(self)
        else:
            self.image_storage = imagestorage.ImageStorage.create(self, self.moncic.config.imagedir)

        # Images contained in the image storage
        self._images: Optional[imagestorage.Images] = None

    def images(self) -> imagestorage.Images:
        """
        Return the Images storage
        """
        if self._images is None:
            self._images = self.enter_context(self.image_storage.images())
        return self._images

    # def __enter__(self):
    #     super().__enter__()
    #     return self
