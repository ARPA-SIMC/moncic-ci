from __future__ import annotations

import contextlib
from typing import Optional, TYPE_CHECKING

from . import imagestorage
from .deb import DebCache

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

        # Debian packages cache
        self._deb_cache: Optional[DebCache] = None

        # /var/cache/apt/archives to be shared by Debian containers
        self._apt_archives: Optional[str] = None

    def images(self) -> imagestorage.Images:
        """
        Return the Images storage
        """
        if self._images is None:
            self._images = self.enter_context(self.image_storage.images())
        return self._images

    def debcache(self) -> DebCache:
        """
        Return the DebCache object to manage an apt package cache
        """
        if self._deb_cache is None:
            self._deb_cache = self.enter_context(DebCache(self.moncic.config.debcachedir))
        return self._deb_cache

    def apt_archives(self) -> str:
        """
        Return the path to a directory that can be bind-mounted as
        /var/cache/apt/archives in Debian containers
        """
        if self._apt_archives is None:
            self._apt_archives = self.enter_context(self.debcache().apt_archives())
        return self._apt_archives
