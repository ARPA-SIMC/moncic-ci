from __future__ import annotations

import argparse
import inspect
import shutil
import textwrap

from ..exceptions import Success


def get_doc_wrapper(lead_width: int) -> textwrap.TextWrapper:
    columns, lines = shutil.get_terminal_size()
    return textwrap.TextWrapper(width=columns - lead_width)


class SourceTypeAction(argparse._StoreAction):
    def __call__(self, parser, namespace, values, option_string=None):
        if values == "list":
            from ..source import source

            source_types = source.registry()

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
    def __call__(self, parser, namespace, values, option_string=None):
        if values == "list":
            from ..build import Build

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
        elif "=" not in values:
            raise ValueError(f"option --option={values!r} must be --option=key=value")
        else:
            k, v = values.split("=", 1)
            if not k:
                raise ValueError(f"option --option={values!r} must have an non-empty key")

            if vals := getattr(namespace, self.dest, None):
                vals[k] = v
            else:
                setattr(namespace, self.dest, {k: v})
