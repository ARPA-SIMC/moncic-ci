from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any

from .source import Source

if TYPE_CHECKING:
    from ..distro import Distro


class URL(Source):
    """
    Remote source as a git URL
    """

    # Remote URL
    url: urllib.parse.ParseResult
    #: Branch to use (default: the current one)
    branch: str | None

    def __init__(self, *, url: urllib.parse.ParseResult, branch: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.url = url
        self.branch = branch

    def make_buildable(self, *, distro: Distro, source_type: str | None = None) -> Source:
        # Clone into a local Git and delegate make_buildable to it
        res = self._git_clone(urllib.parse.urlunparse(self.url), self.branch)
        return res.make_buildable(distro=distro, source_type=source_type)
