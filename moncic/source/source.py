from __future__ import annotations

import abc
import contextlib
import logging
import shlex
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence

import git

from moncic.exceptions import Fail

from ..utils.run import run

if TYPE_CHECKING:
    from .local import LocalSource, Git

log = logging.getLogger(__name__)


class CommandLog(list[str]):
    """
    Log of commands used to create a source
    """

    def add_command(self, *args: str) -> None:
        """
        Add a command to the command log
        """
        self.append(shlex.join(args))

    def run(self, cmd: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
        """
        Run a command and append it to the command log
        """
        self.append(shlex.join(cmd))
        return run(cmd, **kwargs)


class SourceStack(contextlib.ExitStack):
    """
    ExitStack that raises an error if entered multiple times.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entered: bool = False

    def __enter__(self) -> "SourceStack":  # TODO: use Self from 3.11+
        if self.entered:
            raise RuntimeError("__enter__ called in multiple Sources of the same chain")
        super().__enter__()
        self.entered = True
        return self


class Source(abc.ABC):
    """
    Source code to build.

    Not all sources can be built directly: remote URLs need to be cloned
    locally, or local sources need to be prepared for build.

    An initial Source can create a transformed version of itself that can be
    built, tracking the sequence of transformations.

    The initial source needs to be used as a context manager, and serves as
    storage of temporary resources for the sources derived from it.
    """

    #: User-provided name for this resource
    name: str
    #: Source from which this one was generated. None if this is the original source
    parent: Optional["Source"]
    #: ExitStack to use for temporary state
    stack: contextlib.ExitStack
    #: Commands that can be used to recreate this source
    command_log: CommandLog

    def __init__(self, *, name: str | None = None, parent: Source | None = None, command_log: CommandLog | None = None):
        self.parent = parent
        if parent is None:
            self.stack = SourceStack()
        else:
            self.stack = parent.stack
            if name is None:
                name = parent.name
        if name is None:
            raise AttributeError("name not provided, and no parent to use as a fallback")
        self.name = name
        self.command_log = command_log or CommandLog()

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"

    def __enter__(self) -> "Source":
        self.stack.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> Any:
        return self.stack.__exit__(exc_type, exc_val, exc_tb)

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        """
        Add __init__ arguments to kwargs to derive an object from this one
        """
        kwargs["parent"] = self
        kwargs["name"] = self.name

    def derive_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        """
        Create __init__ arguments from this object and user provided values.

        :param kwargs: constructor arguments that add to, or replace, default
                       values constructed from this object.
        """
        new_kwargs: dict[str, Any] = {}
        self.add_init_args_for_derivation(new_kwargs)
        new_kwargs.update(kwargs)
        return new_kwargs

    @classmethod
    def create_local(cls, *, source: str | Path, branch: str | None = None) -> "LocalSource":
        """
        Create a distro-agnostic source from a user-defined string
        """
        base: Source

        # Handle string arguments
        if isinstance(source, str):
            url = urllib.parse.urlparse(source)
            if url.scheme not in ("", "file"):
                from .remote import URL

                base = URL(name=source, url=url)
                return base.clone(branch)
            name = source
            source = Path(url.path)
        else:
            name = source.as_posix()

        # Handle paths
        if source.is_dir():
            if (source / ".git").is_dir():
                from .local import Git

                base = Git(name=name, path=source.absolute())
                if branch:
                    return base.get_branch(branch)
                else:
                    return base
            else:
                from .local import Dir

                if branch is not None:
                    raise Fail("Cannot specify a branch when working on a non-git directory")
                return Dir(name=name, path=source.absolute())
        else:
            from .local import File

            if branch is not None:
                raise Fail("Cannot specify a branch when working on a file")
            return File(name=name, path=source.absolute())

    def _git_clone(self, repository: str, branch: str | None = None) -> "Git":
        """
        Derive a Git source from this one, by cloning a git repository
        Clone this git repository into a temporary working directory.

        Return the path of the new cloned working directory
        """
        from .local import Git

        # Git checkout in a temporary directory
        workdir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        command_log = CommandLog()

        clone_cmd = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", repository]
        if branch is not None:
            clone_cmd += ["--branch", branch]
        command_log.run(clone_cmd, cwd=workdir)

        # Look for the directory that git created
        paths = list(workdir.iterdir())
        if len(paths) != 1:
            raise RuntimeError("git clone created more than one entry in its current directory")

        new_path = paths[0]

        # Recreate remote branches
        repo = git.Repo(new_path)
        for ref in repo.remote().refs:
            name = ref.remote_head
            if name == "HEAD":
                continue
            if name in repo.refs:
                continue
            repo.create_head(name, ref)

        # If we cloned a detached head, create a local branch for it
        if repo.head.is_detached:
            branch = "moncic-ci"
            local_branch = repo.create_head(branch)
            local_branch.checkout()
            command_log.add_command("git", "checkout", "-b", branch)

        return Git(parent=self, path=new_path, repo=repo, readonly=False, command_log=command_log)
