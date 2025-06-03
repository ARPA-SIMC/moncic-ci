import logging
from pathlib import Path

from moncic.image import RunnableImage
from moncic.source.distro import DistroSource
from moncic.source.lint import Reporter, guest_lint

from .base import ContainerSourceOperation

log = logging.getLogger(__name__)


class Query(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    # @override
    # @guest_only
    # def guest_main(self) -> dict[str, Any]:
    #     """
    #     Run the build
    #     """
    #     log.error("NOT YET IMPLEMENTED")
    #     return {}
    #     # self.build.source = self.get_guest_source()
    #     # self.build.build()
    #     # return self.build


class BuildDeps(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    # @override
    # @guest_only
    # def guest_main(self) -> list[str]:
    #     """
    #     Run the build
    #     """
    #     log.error("BUILD DEPS NOT YET IMPLEMENTED")
    #     return []
    #     # self.build.source = self.get_guest_source()
    #     # self.build.build()
    #     # return self.build


class Lint(ContainerSourceOperation):
    """
    Run linter code using a container
    """

    def __init__(
        self,
        image: RunnableImage,
        source: DistroSource,
        *,
        source_artifacts_dir: Path | None = None,
        reporter: Reporter,
    ):
        super().__init__(image, source, source_artifacts_dir=source_artifacts_dir)
        self.reporter = reporter

    # @override
    # @guest_only
    # def guest_main(self) -> Reporter:
    #     """
    #     Run the linter
    #     """
    #     source = self.get_guest_source()
    #     guest_lint(source, self.reporter)
    #     return self.reporter
