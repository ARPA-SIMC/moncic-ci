from __future__ import annotations

import contextlib
from functools import cached_property
from typing import TYPE_CHECKING, Optional

from . import imagestorage
from .deb import DebCache
from .utils import extra_packages_dir

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

    @cached_property
    def images(self) -> imagestorage.Images:
        """
        Return the Images storage
        """
        return self.enter_context(self.image_storage.images())

    @cached_property
    def debcache(self) -> Optional[DebCache]:
        """
        Return the DebCache object to manage an apt package cache
        """
        if (path := self.moncic.config.deb_cache_dir):
            return self.enter_context(DebCache(path))
        else:
            return None

    @cached_property
    def apt_archives(self) -> Optional[str]:
        """
        Return the path of a directory that can be bind-mounted as
        /var/cache/apt/archives in Debian containers
        """
        if (debcache := self.debcache):
            return self.enter_context(debcache.apt_archives())
        else:
            return None

    @cached_property
    def extra_packages_dir(self) -> Optional[str]:
        """
        Return the path of a directory with extra packages to add as a source
        to containers
        """
        if (path := self.moncic.config.extra_packages_dir):
            return self.enter_context(extra_packages_dir(path))
        else:
            return None
