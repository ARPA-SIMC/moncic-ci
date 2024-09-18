from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from . import build
from ..build.utils import link_or_copy
from ..build.build import Build
from ..source.lint import Reporter
from .base import ContainerSourceOperation

if TYPE_CHECKING:
    from ..container import Container, System

log = logging.getLogger(__name__)


class Query(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    @guest_only
    def guest_main(self) -> dict[str, Any]:
        """
        Run the build
        """
        log.error("NOT YET IMPLEMENTED")
        return {}
        # self.build.source = self.get_guest_source()
        # self.build.setup_container_guest(self.system)
        # self.build.build()
        # return self.build


class BuildDeps(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    @guest_only
    def guest_main(self) -> list[str]:
        """
        Run the build
        """
        log.error("BUILD DEPS NOT YET IMPLEMENTED")
        return []
        # self.build.source = self.get_guest_source()
        # self.build.setup_container_guest(self.system)
        # self.build.build()
        # return self.build


class Lint(ContainerSourceOperation):
    """
    Run linter code using a container
    """

    def __init__(self, system: System, source: DistroSource, *, artifacts_dir: Path | None = None, reporter: Reporter):
        super().__init__(system, source, artifacts_dir=artifacts_dir)
        self.reporter = reporter

    @guest_only
    def guest_main(self) -> Reporter:
        """
        Run the linter
        """
        source = self.get_guest_source()
        source.guest_lint(self.reporter)
        return self.reporter
