from __future__ import annotations

import argparse
import shutil
import textwrap

from ..exceptions import Success


class BuildStyleAction(argparse._StoreAction):
    def __call__(self, parser, namespace, values, option_string=None):
        if values == "list":
            from ..build import build

            # Compute width for builder option help
            columns, lines = shutil.get_terminal_size()
            option_name_width = 0
            for build_cls in build.build_types.values():
                for option, option_help in build_cls.list_build_options():
                    if len(option) > option_name_width:
                        option_name_width = len(option)
            option_help_wrapper = textwrap.TextWrapper(width=columns - option_name_width - 4)

            for name, build_cls in sorted(build.build_types.items()):
                print(name)
                for option, option_help in build_cls.list_build_options():
                    for idx, line in enumerate(option_help_wrapper.wrap(option_help)):
                        if idx == 0:
                            print(f"  {option}: {line}")
                        else:
                            print("  {' ' * option_name_width}: {line}")
            raise Success()
        setattr(namespace, self.dest, values)
