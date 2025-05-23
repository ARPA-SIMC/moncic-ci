# TODO: refactor to use, or delete
# import logging
# from pathlib import Path
# from typing import override
#
# from moncic.nspawn.images import NspawnImages
#
# from .image import MockImage
#
# log = logging.getLogger("images")
#
#
# class MockImages(NspawnImages):
#     """
#     Mock image storage, used for testing
#     """
#
#     @override
#     def image(self, name: str) -> MockImage:
#         image = MockImage(images=self, name=name, path=Path("/tmp/mock-moncic-ci"))
#         image.distro = name
#         return image
