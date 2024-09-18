from __future__ import annotations

# Import so they can be registered in monci.MAIN_COMMANDS
from . import build, image, maint, query, run  # noqa
from .base import FAIL_EXCEPTIONS, SUCCESS_EXCEPTIONS, run_main
from .moncic import MAIN_COMMANDS

__all__ = ["run_main", "MAIN_COMMANDS", "FAIL_EXCEPTIONS", "SUCCESS_EXCEPTIONS"]
