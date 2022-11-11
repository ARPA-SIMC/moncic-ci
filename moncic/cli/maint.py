from __future__ import annotations

import logging
import os

from .moncic import MoncicCommand, main_command

log = logging.getLogger(__name__)


@main_command
class Bootstrap(MoncicCommand):
    """
    Create or update the whole set of OS images for the CI
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--recreate", action="store_true",
                            help="delete the images and recreate them from scratch")
        parser.add_argument("systems", nargs="*",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images

            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            systems = images.add_dependencies(systems)

            for name in systems:
                if self.args.recreate:
                    images.remove_system(name)

                try:
                    images.bootstrap_system(name)
                except Exception:
                    log.critical("%s: cannot create image", name, exc_info=True)
                    return 5

                with images.maintenance_system(name) as system:
                    log.info("%s: updating image", name)
                    try:
                        system.update()
                    except Exception:
                        log.critical("%s: cannot update image", name, exc_info=True)
                        return 6


@main_command
class Update(MoncicCommand):
    """
    Update existing OS images
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("systems", nargs="*",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            count_ok = 0
            count_failed = 0

            for name in systems:
                with images.maintenance_system(name) as system:
                    if not os.path.exists(system.path):
                        continue

                    log.info("%s: updating image", name)
                    try:
                        system.update()
                        count_ok += 1
                    except Exception:
                        log.critical("%s: cannot update image", name, exc_info=True)
                        count_failed += 1

            log.info("%d images successfully updated", count_ok)

            if count_failed:
                log.error("%d images failed to update", count_failed)
                return 6


@main_command
class Remove(MoncicCommand):
    """
    Remove existing OS images
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("systems", nargs="+",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        parser.add_argument("--purge", "-P", action="store_true",
                            help="also remove the image configuration file")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            for name in self.args.systems:
                images.remove_system(name)
                if self.args.purge:
                    images.remove_config(name)


@main_command
class Dedup(MoncicCommand):
    """
    Deduplicate disk usage in image directories
    """
    def run(self):
        with self.moncic.session() as session:
            session.images.deduplicate()
