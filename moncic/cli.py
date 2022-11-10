from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Dict, NamedTuple, Optional, Tuple

try:
    import coloredlogs

    HAS_COLOREDLOGS = True
except ModuleNotFoundError:
    HAS_COLOREDLOGS = False


def _get_first_docstring_line(obj: Any) -> Optional[str]:
    try:
        return obj.__doc__.split("\n")[1].strip()
    except (AttributeError, IndexError):
        return None


class Fail(BaseException):
    """
    Failure that causes the program to exit with an error message.

    No stack trace is printed.
    """
    pass


class SharedArgument(NamedTuple):
    """
    Information about an argument shared between a parser and its subparsers
    """
    action: argparse.Action
    args: Tuple[Any]
    kwargs: Dict[str, Any]


class Namespace(argparse.Namespace):
    """
    Hacks around a namespace to allow merging of values set multiple times
    """
    def __setattr__(self, name, value):
        if (arg := self._shared_args.get(name)):
            action_type = arg.kwargs.get("action")
            if action_type == "store_true":
                # OR values
                old = getattr(self, name, False)
                super().__setattr__(name, old or value)
            else:
                raise NotImplementedError("Action {action_type!r} for {arg.action.dest!r} is not supported")
        else:
            return super().__setattr__(name, value)


class ArgumentParser(argparse.ArgumentParser):
    """
    Hacks around a standard ArgumentParser to allow to have a limited set of
    options both outside and inside subcommands
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)

        if not hasattr(self, "shared_args"):
            self.shared_args: Dict[str, SharedArgument] = {}

        # Add arguments from the shared ones
        for a in self.shared_args.values():
            super().add_argument(*a.args, **a.kwargs)

    def add_argument(self, *args, **kw):
        shared = kw.pop("shared", False)
        res = super().add_argument(*args, **kw)
        if shared:
            if (action := kw.get("action")) != "store_true":
                raise NotImplementedError(f"Action {action!r} for {args!r} is not supported")
            # Take note of the argument if it was marked as shared
            self.shared_args[res.dest] = SharedArgument(res, args, kw)
        return res

    def add_subparsers(self, *args, **kw):
        if "parser_class" not in kw:
            kw["parser_class"] = type("ArgumentParser", (self.__class__,), {"shared_args": dict(self.shared_args)})
        return super().add_subparsers(*args, **kw)

    def parse_args(self, *args, **kw):
        if "namespace" not in kw:
            # Use a subclass to pass the special action list without making it
            # appear as an argument
            kw["namespace"] = type("Namespace", (Namespace,), {"_shared_args": self.shared_args})()
        return super().parse_args(*args, **kw)


class Command:
    """
    Base class for actions run from command line
    """

    NAME: Optional[str] = None

    def __init__(self, args: argparse.Namespace):
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
    def make_subparser(cls, subparsers):
        if cls.NAME is None:
            cls.NAME = cls.__name__.lower()
        parser = subparsers.add_parser(
            cls.NAME,
            help=_get_first_docstring_line(cls),
        )
        parser.set_defaults(handler=cls)
        return parser


def run(func):
    try:
        sys.exit(func())
    except Fail as e:
        print(e, file=sys.stderr)
        sys.exit(1)
