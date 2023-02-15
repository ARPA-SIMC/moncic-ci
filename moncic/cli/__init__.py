from __future__ import annotations

from .base import run_main, FAIL_EXCEPTIONS, SUCCESS_EXCEPTIONS
from .moncic import MAIN_COMMANDS

# Import so they can be registered in monci.MAIN_COMMANDS
from . import build, image, run, maint, query  # noqa


__all__ = ["run_main", "MAIN_COMMANDS", "FAIL_EXCEPTIONS", "SUCCESS_EXCEPTIONS"]
