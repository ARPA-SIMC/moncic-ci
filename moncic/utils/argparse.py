import argparse
from typing import Any, NamedTuple, override


class SharedArgument(NamedTuple):
    """
    Information about an argument shared between a parser and its subparsers
    """

    action: argparse.Action
    args: tuple[Any]
    kwargs: dict[str, Any]


class Namespace(argparse.Namespace):
    """
    Hacks around a namespace to allow merging of values set multiple times
    """

    @override
    def __setattr__(self, name: str, value: Any) -> Any:
        if arg := self._shared_args.get(name):
            action_type = arg.kwargs.get("action")
            if action_type == "store_true":
                # OR values
                old = getattr(self, name, False)
                super().__setattr__(name, old or value)
            elif action_type == "store":
                old = getattr(self, name, False)
                if old is None:
                    super().__setattr__(name, value)
                elif old != value:
                    raise argparse.ArgumentError(
                        arg, f"conflicting values provided for {arg.action.dest!r} ({old!r} and {value!r})"
                    )
            else:
                raise NotImplementedError("Action {action_type!r} for {arg.action.dest!r} is not supported")
        else:
            return super().__setattr__(name, value)


class ArgumentParser(argparse.ArgumentParser):
    """
    Hacks around a standard ArgumentParser to allow to have a limited set of
    options both outside and inside subcommands
    """

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)

        if not hasattr(self, "shared_args"):
            self.shared_args: dict[str, SharedArgument] = {}

        # Add arguments from the shared ones
        for a in self.shared_args.values():
            super().add_argument(*a.args, **a.kwargs)

    @override
    def add_argument(self, *args: Any, **kw: Any) -> Any:
        shared = kw.pop("shared", False)
        res = super().add_argument(*args, **kw)
        if shared:
            if (action := kw.get("action")) not in ("store", "store_true"):
                raise NotImplementedError(f"Action {action!r} for {args!r} is not supported")
            # Take note of the argument if it was marked as shared
            self.shared_args[res.dest] = SharedArgument(res, args, kw)
        return res

    @override
    def add_subparsers(self, **kwargs: Any) -> Any:
        if "parser_class" not in kwargs:
            kwargs["parser_class"] = type("ArgumentParser", (self.__class__,), {"shared_args": dict(self.shared_args)})
        return super().add_subparsers(**kwargs)

    @override
    def parse_args(self, *args: Any, **kw: Any) -> Any:
        if "namespace" not in kw:
            # Use a subclass to pass the special action list without making it
            # appear as an argument
            kw["namespace"] = type("Namespace", (Namespace,), {"_shared_args": self.shared_args})()
        return super().parse_args(*args, **kw)
