from __future__ import annotations

import argparse
import configparser
import inspect
import shutil
import textwrap
from dataclasses import fields
from typing import TYPE_CHECKING, Any

from ..exceptions import Fail, Success

if TYPE_CHECKING:
    from moncic.build import Build


def get_doc_wrapper(lead_width: int) -> textwrap.TextWrapper:
    columns, lines = shutil.get_terminal_size()
    return textwrap.TextWrapper(width=columns - lead_width)


class SourceTypeAction(argparse._StoreAction):
    def __call__(self, parser, namespace, values, option_string=None):
        if values == "list":
            from ..source.distro import source_types

            # Compute width for source names
            name_width = max(len(x) for x in source_types.keys())
            help_wrapper = get_doc_wrapper(name_width + 2)

            for name, source_cls in sorted(source_types.items()):
                for idx, line in enumerate(help_wrapper.wrap(inspect.getdoc(source_cls))):
                    if idx == 0:
                        print(f"{name.rjust(name_width)}: {line}")
                    else:
                        print(f"{' ' * name_width}  {line}")
            raise Success()
        setattr(namespace, self.dest, values)


class BuildOptionAction(argparse._AppendAction):
    """
    argparse action to collect build options.

    Autodetect possible assignments from the Build class field list.

    Support 'list' to list available assignments.

    Namespace value is set to a dict that can be passed to constructors of
    Build subclasses.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        from ..build import Build

        if values == "list":
            # Compute width for option names
            name_width = max(len(x) for cls in Build.list_build_classes() for x, doc in cls.list_build_options())
            help_wrapper = get_doc_wrapper(name_width + 4)

            for cls in Build.list_build_classes():
                print(f"{cls.get_name()}:")
                for name, doc in cls.list_build_options():
                    for idx, line in enumerate(help_wrapper.wrap(doc)):
                        if idx == 0:
                            print(f"  {name.rjust(name_width)}: {line}")
                        else:
                            print(f"  {' ' * name_width}  {line}")
            raise Success()

        if "=" not in values:
            raise ValueError(f"option --option={values!r} must be --option=key=value")

        k, v = values.split("=", 1)
        if not k:
            raise ValueError(f"option --option={values!r} must have an non-empty key")

        allowed_keys: set[str] = set()
        for cls in Build.list_build_classes():
            for name, doc in cls.list_build_options():
                allowed_keys.add(name)

        if k not in allowed_keys:
            raise ValueError(f"option --option={values!r} has unsupported key {k!r}")

        if vals := getattr(namespace, self.dest, None):
            vals[k] = v
        else:
            setattr(namespace, self.dest, {k: v})


def set_build_option_action(build: Build, key: str, val: Any) -> None:
    """
    Set a build option action in a builder instance
    """
    for field in fields(build):
        if field.name == key:
            break
    else:
        raise Fail(f"cannot set option {key!r} on build of type {type(build).__name__}")

    if field.type == "bool":
        if isinstance(val, bool):
            setattr(build, key, val)
        elif isinstance(val, str):
            bool_value = configparser.ConfigParser.BOOLEAN_STATES.get(val)
            if bool_value is None:
                raise Fail(f"cannot parse value of {key}={val!r} as a boolean")
            setattr(build, key, bool_value)
        else:
            raise TypeError(f"trying to set {key} (of type bool) to {val!r}")
    else:
        setattr(build, key, val)
