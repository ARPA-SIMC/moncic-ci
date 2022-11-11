from __future__ import annotations

import argparse
from typing import Any, Dict, NamedTuple, Tuple


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
