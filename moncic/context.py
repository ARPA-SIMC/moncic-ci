from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .container import Container
    from .moncic import Moncic
    from .session import Session
    from .system import System

# Current Moncic instance
moncic: ContextVar[Moncic] = ContextVar("moncic")

# Current Moncic session
session: ContextVar[Session] = ContextVar("session")

# Current Moncic system (set only when executing in a container)
system: ContextVar[System] = ContextVar("system")

# Current Moncic container (set only when executing in a container)
container: ContextVar[Container] = ContextVar("container")
