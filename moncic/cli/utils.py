from __future__ import annotations

import argparse
import inspect
import shutil
import textwrap

from ..exceptions import Success


class SourceTypeAction(argparse._StoreAction):
    def __call__(self, parser, namespace, values, option_string=None):
        if values == "list":
            from ..source import source
            source_types = source.registry()

            # Compute width for builder option help
            columns, lines = shutil.get_terminal_size()
            name_width = 0
            for name in source_types.keys():
                if len(name) > name_width:
                    name_width = len(name)
            option_help_wrapper = textwrap.TextWrapper(width=columns - name_width - 2)

            for name, source_cls in sorted(source_types.items()):
                for idx, line in enumerate(option_help_wrapper.wrap(inspect.getdoc(source_cls))):
                    if idx == 0:
                        print(f"{name.rjust(name_width)}: {line}")
                    else:
                        print(f"{' ' * name_width}  {line}")
            raise Success()
        setattr(namespace, self.dest, values)
