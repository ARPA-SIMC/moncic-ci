from __future__ import annotations

import contextlib
import contextvars
import re
import shlex
import subprocess
import sys
from functools import cached_property
from typing import TYPE_CHECKING, Any

from . import context, imagestorage
from .utils.deb import DebCache
from .utils.fs import extra_packages_dir

if TYPE_CHECKING:
    from .moncic import Moncic


class Session(contextlib.ExitStack):
    """
    Hold shared resourcse during a single-threaded Moncic-CI work session
    """

    def __init__(self, moncic: Moncic):
        super().__init__()
        self.moncic = moncic
        self.orig_moncic: contextvars.Token | None = None
        self.orig_session: contextvars.Token | None = None

        # Storage for OS images
        self.image_storage = self._instantiate_imagestorage()

    def _instantiate_imagestorage(self) -> imagestorage.ImageStorage:
        if self.moncic.config.imagedir is None:
            return imagestorage.ImageStorage.create_default(self)
        else:
            return imagestorage.ImageStorage.create_from_path(self, self.moncic.config.imagedir)

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
    def debcache(self) -> DebCache | None:
        """
        Return the DebCache object to manage an apt package cache
        """
        if path := self.moncic.config.deb_cache_dir:
            return self.enter_context(DebCache(path))
        else:
            return None

    @cached_property
    def apt_archives(self) -> str | None:
        """
        Return the path of a directory that can be bind-mounted as
        /var/cache/apt/archives in Debian containers
        """
        if debcache := self.debcache:
            return self.enter_context(debcache.apt_archives())
        else:
            return None

    @cached_property
    def extra_packages_dir(self) -> str | None:
        """
        Return the path of a directory with extra packages to add as a source
        to containers
        """
        if path := self.moncic.config.extra_packages_dir:
            return self.enter_context(extra_packages_dir(path))
        else:
            return None


class MockSession(Session):
    """
    Mock session used for tests
    """

    def __init__(self, moncic: Moncic):
        super().__init__(moncic)
        self.log: list[dict[str, Any]] = []
        self.process_result_queue: dict[str, subprocess.CompletedProcess] = {}

    def mock_log(self, **kwargs: Any):
        caller_stack = sys._getframe(1)
        kwargs.setdefault("func", caller_stack.f_code.co_name)
        self.log.append(kwargs)

    def get_process_result(self, *, args: list[str]) -> subprocess.CompletedProcess:
        cmdline = " ".join(shlex.quote(c) for c in args)
        for regex, result in self.process_result_queue.items():
            if re.search(regex, cmdline):
                self.process_result_queue.pop(regex)
                result.args = args
                return result
        return subprocess.CompletedProcess(args=args, returncode=0)

    def set_process_result(
        self,
        regex: str,
        *,
        returncode: int = 0,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
    ):
        self.process_result_queue[regex] = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _instantiate_imagestorage(self) -> imagestorage.ImageStorage:
        return imagestorage.ImageStorage.create_mock(self)

    @cached_property
    def debcache(self) -> DebCache | None:
        """
        Return the DebCache object to manage an apt package cache
        """
        return None

    @cached_property
    def apt_archives(self) -> str | None:
        """
        Return the path of a directory that can be bind-mounted as
        /var/cache/apt/archives in Debian containers
        """
        return None

    @cached_property
    def extra_packages_dir(self) -> str | None:
        """
        Return the path of a directory with extra packages to add as a source
        to containers
        """
        return None
