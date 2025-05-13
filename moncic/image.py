import dataclasses
import enum
import logging

log = logging.getLogger("image")


class ImageType(enum.StrEnum):
    """Identify an image type."""

    NSPAWN = "nspawn"
    PODMAN = "podman"


@dataclasses.dataclass(kw_only=True)
class Image:
    """
    Identify an image from which systems can be started.
    """

    #: Container type
    image_type: ImageType = dataclasses.field(init=False)
    #: Image name
    name: str

    @property
    def logger(self):
        """
        Return a logger for this system
        """
        return logging.getLogger(f"system.{self.name}")
