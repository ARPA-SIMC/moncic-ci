from __future__ import annotations

import io
import os
import subprocess
import tempfile
from typing import Optional

import yaml


def edit_yaml(buf: str, path: str) -> Optional[str]:
    """
    Open an editor on buf and validate its result as YAML.

    path is only used for error messages.

    Return the edited text contents.

    Return None if editing did not change the contents.
    """
    ERROR_MARKER = "# ----- this line and everything below it will be ignored -----"
    editor = os.environ.get("EDITOR", "sensible-editor")

    current = buf
    error = None

    while True:
        with tempfile.NamedTemporaryFile(
                mode="wt",
                suffix=".yaml") as tf:
            # Write out the current buffer
            tf.write(current)
            # Write lines to communicate a parser error, if needed
            if error is not None:
                print(ERROR_MARKER, file=tf)
                print("#", file=tf)
                print(f"# Original file: {path}", file=tf)
                print("#", file=tf)
                print("# Quit with no modifications to restore the original.", file=tf)
                print("#", file=tf)
                print("# Error:", file=tf)
                for line in error.splitlines():
                    print(f"# {line}", file=tf)
                error = None
            tf.flush()

            # Run the editor on it
            subprocess.run([editor, tf.name], check=True)

            # Reopen by name in case the editor did not write on the same
            # inode
            with open(tf.name, "rt") as fd:
                lines = []
                for line in fd:
                    if line.startswith(ERROR_MARKER):
                        break
                    lines.append(line)
                edited = "".join(lines)

            if edited == current:
                return None

            try:
                with io.StringIO(edited) as buf:
                    yaml.load(buf, Loader=yaml.CLoader)
                return edited
            except Exception as e:
                error = str(e)
            current = edited
