from __future__ import annotations

import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import git

from .exceptions import Fail
from .container import RunConfig

if TYPE_CHECKING:
    from .build import Builder


@dataclass
class Source:
    """
    Sources to be built
    """
    # Original source as specified by the user
    source: str
    # Path to the unpacked sources in the host system
    host_path: str

    @classmethod
    def _create_local(cls, builder: Builder, path: str, branch: Optional[str]) -> "Source":
        """
        Create a Source class given a local path
        """
        if os.path.isdir(path):
            if os.path.isdir(os.path.join(path, ".git")):
                return Git._create_local(builder, path, branch)
            elif os.path.isdir(os.path.join(path, "debian")):
                return DebianSourceDir(path)
            else:
                # TODO: find specfiles?
                raise Fail(f"{path!r}: cannot detect source type")
        elif path.endswith(".dsc"):
            return DebianSourcePackage(path)
        else:
            raise Fail(f"{path!r}: cannot detect source type")

    @classmethod
    def create(cls, builder: Builder, source: str, branch: Optional[str] = None) -> "Source":
        """
        Create a Source class for the given source path/url
        """
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme == '':
            return cls._create_local(builder, source, branch)

        if parsed.scheme == "file":
            return cls._create_local(builder, parsed.path, branch)

        return Git._create_clone(builder, source, branch)


@dataclass
class Git(Source):
    """
    Local git working directory
    """
    # local: do not touch
    # if the current branch is not the active branch:
    #   copy: checkout the right branch
    # container: gbp dance?

    @classmethod
    def _create_local(cls, builder: Builder, path: str, branch: Optional[str]):
        repo_abspath = os.path.abspath(path)
        gitrepo = git.Repo(repo_abspath)
        if branch is None or gitrepo.active_branch == branch:
            return cls(path, host_path=repo_abspath)

        # We need to checkout the right branch
        return cls._create_clone(builder, path, branch)

    @classmethod
    def _create_clone(cls, builder: Builder, url: str, branch: Optional[str]):
        """
        Create a Source class given a remote URL
        """
        # Git checkout in a temporary directory
        workdir = builder.enter_context(tempfile.TemporaryDirectory())
        cmd = ["git", "-c", "advice.detachedHead=false", "clone", url]
        if branch is not None:
            cmd += ["--branch", branch]
        builder.system.local_run(cmd, config=RunConfig(cwd=workdir))

        # Look for the directory that git created
        names = os.listdir(workdir)
        if len(names) != 1:
            raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")

        # Instantiate a Git source
        return cls(url, host_path=os.path.join(workdir, names[0]))


@dataclass
class DebianSourceDir(Source):
    """
    Unpacked debian source
    """
    # local: collect orig tarball in directory above
    def __init__(self, *args, **kw):
        raise NotImplementedError("DebianSourceDir not yet implemented")


@dataclass
class DebianSourcePackage(Source):
    """
    Debian source .dsc
    """
    # local: collect file list
    # container: unpack
    def __init__(self, *args, **kw):
        raise NotImplementedError("DebianSourcePackage not yet implemented")
