from __future__ import annotations

import contextlib
import contextvars
from functools import cached_property
from typing import TYPE_CHECKING, Optional

from . import imagestorage
from .utils.deb import DebCache
from .utils.fs import extra_packages_dir
from . import context

if TYPE_CHECKING:
    from .moncic import Moncic


class Session(contextlib.ExitStack):
    """
    Hold shared resourcse during a single-threaded Moncic-CI work session
    """
    def __init__(self, moncic: Moncic):
        super().__init__()
        self.moncic = moncic
        self.orig_moncic: Optional[contextvars.Token] = None
        self.orig_session: Optional[contextvars.Token] = None

        # Storage for OS images
        self.image_storage: imagestorage.ImageStorage
        if self.moncic.config.imagedir is None:
            self.image_storage = imagestorage.ImageStorage.create_default(self)
        else:
            self.image_storage = imagestorage.ImageStorage.create(self, self.moncic.config.imagedir)

    def __enter__(self):
        self.orig_moncic = context.moncic.set(self.moncic)
        self.orig_session = context.session.set(self)
        return super().__enter__()

    def __exit__(self, *args):
        res = super().__exit__(*args)
        if self.orig_session is not None:
            context.session.reset(self.orig_session)
        if self.orig_moncic is not None:
            context.moncic.set(self.orig_moncic)
        return res

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
