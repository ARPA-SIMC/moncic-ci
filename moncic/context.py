from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from .utils.privs import ProcessPrivs

if TYPE_CHECKING:
    from .container import Container
    from .image import Image
    from .moncic import Moncic
    from .session import Session

#: Current Moncic instance
moncic: ContextVar[Moncic] = ContextVar("moncic")

#: Current Moncic session
session: ContextVar[Session] = ContextVar("session")

#: Current Moncic system (set only when executing in a container)
image: ContextVar[Image] = ContextVar("image")

#: Current Moncic container (set only when executing in a container)
container: ContextVar[Container] = ContextVar("container")

#: Manage root privileges
privs: ProcessPrivs = ProcessPrivs()

#: Running in debugging mode
debug: ContextVar[bool] = ContextVar("debug")
