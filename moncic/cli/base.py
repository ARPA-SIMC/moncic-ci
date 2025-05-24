from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Never, Callable

try:
    import coloredlogs

    HAS_COLOREDLOGS = True
except ModuleNotFoundError:
    HAS_COLOREDLOGS = False


def _get_first_docstring_line(obj: Any) -> str | None:
    try:
        return str(obj.__doc__).split("\n")[1].strip()
    except (AttributeError, IndexError):
        return None


FAIL_EXCEPTIONS: list[type[BaseException]] = []
SUCCESS_EXCEPTIONS: list[type[BaseException]] = []


class Command:
    """
    Base class for actions run from command line
    """

    NAME: str | None = None

    def __init__(self, args: argparse.Namespace) -> None:
        if self.NAME is None:
            self.NAME = self.__class__.__name__.lower()
        self.args = args
        self.setup_logging()

    def setup_logging(self) -> None:
        FORMAT = "%(asctime)-15s %(levelname)s %(name)s %(message)s"
        if self.args.debug:
            level = logging.DEBUG
        elif self.args.verbose:
            level = logging.INFO
        else:
            level = logging.WARN

        if HAS_COLOREDLOGS:
            coloredlogs.install(level=level, fmt=FORMAT)
        else:
            logging.basicConfig(level=level, stream=sys.stderr, format=FORMAT)

    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        if cls.NAME is None:
            cls.NAME = cls.__name__.lower()
        parser: argparse.ArgumentParser = subparsers.add_parser(
            cls.NAME,
            help=_get_first_docstring_line(cls),
        )
        parser.set_defaults(handler=cls)
        return parser


def run_main(func: Callable[[], int | None]) -> Never:
    try:
        sys.exit(func())
    except tuple(FAIL_EXCEPTIONS) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except tuple(SUCCESS_EXCEPTIONS):
        sys.exit(0)
