import urllib.parse
from typing import Any, override

from .local import Git
from .source import Source


class URL(Source):
    """
    Remote source as a git URL
    """

    # Remote URL
    url: urllib.parse.ParseResult
    #: Branch to use (default: the current one)
    branch: str | None

    def __init__(self, *, url: urllib.parse.ParseResult, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.url = url

    @override
    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["url"] = self.url

    def clone(self, branch: str | None = None) -> Git:
        """
        Clone the repository into a local source
        """
        return self._git_clone(self.name, branch)
