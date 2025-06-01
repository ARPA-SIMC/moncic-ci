import shlex
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from moncic import context


class Script:
    """Incrementally build a shellscript."""

    def __init__(self, title: str, *, cwd: Path | None = None, root: bool = False) -> None:
        self.title = title
        self.cwd = cwd
        self.root = root
        self.debug_mode = context.debug.get()
        if self.debug_mode:
            self.shell = "/bin/sh -uxe"
        else:
            self.shell = "/bin/sh -ue"
        self.lines: list[str] = []
        self.indent = 0

    def __bool__(self) -> bool:
        """Check if the script contains any command."""
        return bool(self.lines)

    def add_line(self, line: str) -> None:
        self.lines.append(" " * self.indent + line)

    def run_unquoted(self, command: str, *, description: str | None = None) -> None:
        if description:
            self.add_line("echo " + shlex.quote(description))
        self.add_line(command)

    def run(
        self,
        command: list[str],
        *,
        description: str | None = None,
        output: Path | None = None,
        cwd: Path | None = None,
        check: bool = True,
    ) -> None:
        """Append a command to the script."""
        if description:
            self.add_line("echo " + shlex.quote(description))
        cmd = shlex.join(command)
        if output:
            cmd += f" > {shlex.quote(output.as_posix())}"
        if cwd:
            cmd = "(cd {shlex.quote(cwd.as_posix())} && {cmd})"
        if not check:
            cmd += " || true"
        self.add_line(cmd)

    @contextmanager
    def if_(self, condition: str | Iterable[str]) -> Generator[None, None, None]:
        """Delimit a conditional block."""
        if not isinstance(condition, str):
            condition = shlex.join(condition)
        self.add_line(f"if {condition}")
        self.add_line("then")
        self.indent += 4
        try:
            yield
        finally:
            self.indent -= 4
            self.add_line("fi")

    @contextmanager
    def for_(self, var: str, generator: str | Iterable[str]) -> Generator[None, None, None]:
        """Delimit a for loop."""
        if not isinstance(generator, str):
            generator = shlex.join(generator)
        self.add_line(f"for {var} in {generator}")
        self.add_line("do")
        self.indent += 4
        try:
            yield
        finally:
            self.indent -= 4
            self.add_line("done")

    def fail(self, message: str) -> None:
        """
        Append an error message that terminates the script with an error.

        The message will undergo double-quote shell interpolation.
        """
        self.add_line(f'echo "{message}" >&2')
        self.add_line("exit 1")

    def debug(self, command: list[str], *, description: str | None = None) -> None:
        """Append a command generating debugging output."""
        if not self.debug_mode:
            return
        if description:
            self.add_line("echo " + shlex.quote(description))
        self.add_line(shlex.join(command) + " >&2")

    def write(self, path: Path, contents: str, description: str | None = None) -> None:
        """Append a command to write data to a file."""
        if description:
            self.add_line("echo " + shlex.quote(description))
        self.add_line(f"echo {shlex.quote(contents)} > {shlex.quote(path.as_posix())}")

    def print(self, file: IO[str] | None = None) -> None:
        """Write the script to a file."""
        print(f"#!{self.shell}", file=file)
        print(file=file)
        print(f"# {self.title}", file=file)
        print(file=file)
        for line in self.lines:
            print(line, file=file)
