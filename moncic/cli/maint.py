import argparse
import logging
from typing import Any, override

from moncic.image import RunnableImage

from .moncic import MoncicCommand, main_command

log = logging.getLogger(__name__)


@main_command
class Bootstrap(MoncicCommand):
    """
    Create or update the whole set of OS images for the CI
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument("--recreate", action="store_true", help="delete the images and recreate them from scratch")
        parser.add_argument(
            "images",
            nargs="+",
            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images",
        )
        return parser

    def run(self) -> int | None:
        with self.moncic.session() as session:
            images = session.images
            names = self.args.images
            for name in names:
                image = images.image(name)

                if image.bootstrapped:
                    assert isinstance(image, RunnableImage)
                    if self.args.recreate:
                        bootstrappable_image = image.remove()
                    else:
                        return None

                assert bootstrappable_image is not None
                try:
                    image = bootstrappable_image.bootstrap()
                except Exception:
                    log.critical("%s: cannot create image", name, exc_info=True)
                    return 5

                log.info("%s: updating image", name)
                try:
                    image.update()
                except Exception:
                    log.critical("%s: cannot update image", name, exc_info=True)
                    return 6
        return None


@main_command
class Update(MoncicCommand):
    """
    Update existing OS images
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument(
            "systems",
            nargs="*",
            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images",
        )
        return parser

    def run(self) -> int | None:
        with self.moncic.session() as session:
            images = session.images
            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            count_ok = 0
            count_failed = 0

            for name in systems:
                image = images.image(name)
                if not image.bootstrapped:
                    continue
                assert isinstance(image, RunnableImage)
                log.info("%s: updating image", name)
                try:
                    image.update()
                    count_ok += 1
                except Exception:
                    log.critical("%s: cannot update image", name, exc_info=True)
                    count_failed += 1

            log.info("%d images successfully updated", count_ok)

            if count_failed:
                log.error("%d images failed to update", count_failed)
                return 6
        return None


@main_command
class Remove(MoncicCommand):
    """
    Remove existing OS images
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument(
            "systems",
            nargs="+",
            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images",
        )
        parser.add_argument("--purge", "-P", action="store_true", help="also remove the image configuration file")
        return parser

    def run(self) -> None:
        with self.moncic.session() as session:
            images = session.images
            for name in self.args.systems:
                image = images.image(name)
                assert isinstance(image, RunnableImage)
                bootstrappable_image = image.remove()
                if self.args.purge:
                    assert bootstrappable_image is not None
                    bootstrappable_image.remove_config()


@main_command
class Dedup(MoncicCommand):
    """
    Deduplicate disk usage in image directories
    """

    def run(self) -> None:
        with self.moncic.session() as session:
            session.images.deduplicate()
