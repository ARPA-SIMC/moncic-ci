from typing import IO
from pathlib import Path
import shlex


class Script:
    """Incrementally build a shellscript."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.shell = "/bin/sh -uxe"
        self.lines: list[str] = []

    def __bool__(self) -> bool:
        """Check if the script contains any command."""
        return bool(self.lines)

    def run(
        self, command: list[str], *, description: str | None = None, output: Path | None = None, cwd: Path | None = None
    ) -> None:
        """Append a command to the script."""
        if description:
            self.lines.append("echo " + shlex.quote(description))
        cmd = shlex.join(command)
        if output:
            cmd += f" > {shlex.quote(output.as_posix())}"
        if cwd:
            cmd = "(cd {shlex.quote(cwd.as_posix())} && {cmd})"
        self.lines.append(cmd)

    def debug(self, command: list[str], *, description: str | None = None) -> None:
        """Append a command to the script."""
        if description:
            self.lines.append("echo " + shlex.quote(description))
        self.lines.append(shlex.join(command) + " >&2")

    def write(self, path: Path, contents: str, description: str | None = None) -> None:
        """Append a command to write data to a file."""
        if description:
            self.lines.append("echo " + shlex.quote(description))
        self.lines.append(f"echo {shlex.quote(contents)} > {shlex.quote(path.as_posix())}")

    def print(self, file: IO[str] | None = None) -> None:
        """Write the script to a file."""
        print(f"#!{self.shell}", file=file)
        print(file=file)
        print(f"# {self.title}", file=file)
        print(file=file)
        for line in self.lines:
            print(line, file=file)
