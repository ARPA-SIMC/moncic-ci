from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .source import Source


class Reporter:
    """
    Report lint result
    """

    def __init__(self) -> None:
        #: Number of errors found
        self.error_count: int = 0
        #: Number of warnings found
        self.warning_count: int = 0

    def error(self, source: Source, message: str):
        print(message)
        self.error_count += 1

    def warning(self, source: Source, message: str):
        print(message)
        self.warning_count += 1
