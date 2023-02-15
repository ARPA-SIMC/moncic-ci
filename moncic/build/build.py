from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from ..source import Source
from ..utils.guest import guest_only, host_only

if TYPE_CHECKING:
    from ..container import Container


@dataclass
class Build:
    """
    Information gathered during a build
    """
    # Path to source to be built
    source: Source
    # Package name (optional when not yet set)
    name: Optional[str] = None
    # True if the build was successful
    success: bool = False
    # Directory where artifacts are copied after the build. Artifacts are lost
    # when not set
    artifacts_dir: Optional[str] = None
    # Set to True to only build source packages, and skip compiling/building
    # binary packages
    source_only: bool = False
    # List of container paths for artifacts
    artifacts: list[str] = field(default_factory=list)

    @guest_only
    def build(self):
        """
        Run the build.

        The function will be called inside the running system.

        The current directory will be set to the source directory in /srv/moncic-ci/source/<name>.

        Standard output and standard error are logged.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build is not implemented")

    @host_only
    def setup_container_host(self, container: Container):
        """
        Hook to run setup functions in the host container
        """
        # TODO: remove in favour of something more specific
        pass

    @guest_only
    def setup_container_guest(self):
        """
        Set up the build environment in the container
        """
        pass
