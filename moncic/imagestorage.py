from __future__ import annotations

import abc
import contextlib
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .images import Images
    from .session import Session

log = logging.getLogger("images")


class ImageStorage(abc.ABC):
    """
    Interface for handling image storage.

    This allows to manage access to image repositories, some of which may need
    to be activated before use and deactivated after use.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @abc.abstractmethod
    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        """
        Make the image storage accessible for the duration of this context
        manager
        """

    @classmethod
    def create_mock(cls, session: Session) -> ImageStorage:
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        raise NotImplementedError()
        # from .mock.imagestorage import NspawnImageStorage

        # return NspawnImageStorage.create_mock(session)
