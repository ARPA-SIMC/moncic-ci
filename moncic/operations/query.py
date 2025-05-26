from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from ..source.lint import Reporter, guest_lint
from ..utils.guest import guest_only
from .base import ContainerSourceOperation

if TYPE_CHECKING:
    from moncic.image import Image
    from moncic.source.distro import DistroSource

log = logging.getLogger(__name__)


class Query(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    @override
    @guest_only
    def guest_main(self) -> dict[str, Any]:
        """
        Run the build
        """
        log.error("NOT YET IMPLEMENTED")
        return {}
        # self.build.source = self.get_guest_source()
        # self.build.build()
        # return self.build


class BuildDeps(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    @override
    @guest_only
    def guest_main(self) -> list[str]:
        """
        Run the build
        """
        log.error("BUILD DEPS NOT YET IMPLEMENTED")
        return []
        # self.build.source = self.get_guest_source()
        # self.build.build()
        # return self.build


class Lint(ContainerSourceOperation):
    """
    Run linter code using a container
    """

    def __init__(self, image: Image, source: DistroSource, *, artifacts_dir: Path | None = None, reporter: Reporter):
        super().__init__(image, source, artifacts_dir=artifacts_dir)
        self.reporter = reporter

    @override
    @guest_only
    def guest_main(self) -> Reporter:
        """
        Run the linter
        """
        source = self.get_guest_source()
        guest_lint(source, self.reporter)
        return self.reporter
