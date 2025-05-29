# Import to make sure classes can register themselves with DistroFamily
from . import debian, rpm  # noqa
from .distro import Distro, DistroFamily

__all__ = ["DistroFamily", "Distro"]
