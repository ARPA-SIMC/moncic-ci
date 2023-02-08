from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import git

from .source import Source, register

if TYPE_CHECKING:
    from ..build import Builder

log = logging.getLogger(__name__)


@dataclass
class RPMGit(Source):
    """
    Git working directory with a Debian package
    """
    # TODO: locate specfile
    @classmethod
    def _create_from_repo(cls, builder: Builder, source: str, repo: git.Repo, cloned: bool) -> "RPMGit":
        """
        Create a Git Source from a prepared host path
        """
        travis_yml = os.path.join(source.host_path, ".travis.yml")
        try:
            with open(travis_yml, "rt") as fd:
                if 'simc/stable' in fd.read():
                    return ARPAGit._create_from_repo(builder, source, repo, cloned)
        except FileNotFoundError:
            pass

        raise NotImplementedError("RPM source found, but simc/stable not found in .travis.yml for ARPA builds")


@register
@dataclass
class ARPAGit(RPMGit):
    """
    ARPA/SIMC git repository, building RPM packages using the logic previously
    configured for travis
    """
    NAME = "rpm-arpa"

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: str, repo: git.Repo, cloned: bool) -> "RPMGit":
        return cls(source, repo.working_dir)
