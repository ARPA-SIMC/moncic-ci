from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Dict, List, Optional, Tuple

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


class Namespace(argparse.Namespace):
    """
    Hacks around a namespace to allow merging of values set multiple times
    """
    def __setattr__(self, name, value):
        if name not in self._cli_shared_actions:
            return super().__setattr__(name, value)

        action, kw = self._cli_shared_actions[name]
        action_type = kw.get("action")
        if action_type == "store_true":
            # OR values
            old = getattr(self, name, False)
            super().__setattr__(name, old or value)
        else:
            raise NotImplementedError("Action {action_type!r} for {action.dest!r} is not supported")


class ArgumentParser(argparse.ArgumentParser):
    """
    Hacks around a standard ArgumentParser to allow to have a limited set of
    options both outside and inside subcommands
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.cli_shared_args: List[argparse.Action, Tuple[Any], Dict[str, Any]] = []

    def add_argument(self, *args, **kw):
        shared = kw.pop("shared", False)
        res = super().add_argument(*args, **kw)
        if shared:
            if (action := kw.get("action")) != "store_true":
                raise NotImplementedError(f"Action {action!r} for {args!r} is not supported")
            self.cli_shared_args.append((res, args, kw))
        return res

    def add_subparsers(self, *args, **kw):
        res = super().add_subparsers(*args, **kw)
        setattr(res, "cli_shared_args", self.cli_shared_args)
        return res

    def parse_args(self, *args, **kw):
        if "namespace" not in kw:
            # Use a subclass to pass the special action list without making it
            # appear as an argument
            cli_shared_actions = {action.dest: (action, kw) for action, args, kw in self.cli_shared_args}
            kw["namespace"] = type("Namespace", (Namespace,), {"_cli_shared_actions": cli_shared_actions})()
        res = super().parse_args(*args, **kw)
        return res


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
        if cli_shared_args := getattr(subparsers, "cli_shared_args", None):
            for action, args, kw in cli_shared_args:
                parser.add_argument(*args, **kw)
        return parser


def run(func):
    try:
        sys.exit(func())
    except Fail as e:
        print(e, file=sys.stderr)
        sys.exit(1)
