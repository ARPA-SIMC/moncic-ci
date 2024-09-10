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
from .base import ContainerSourceOperation

if TYPE_CHECKING:
    from ..container import Container, System

log = logging.getLogger(__name__)


class Query(ContainerSourceOperation):
    """
    Query informations about a Source using a container
    """

    @guest_only
    def guest_main(self) -> build.Build:
        """
        Run the build
        """
        log.error("NOT YET IMPLEMENTED")
        return {}
        # self.build.source = self.get_guest_source()
        # self.build.setup_container_guest(self.system)
        # self.build.build()
        # return self.build
