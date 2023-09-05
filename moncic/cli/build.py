from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from ..build import Builder
from ..distro import Distro
from ..source import InputSource
from .moncic import SourceCommand, main_command
from .utils import BuildOptionAction

log = logging.getLogger(__name__)


@main_command
class CI(SourceCommand):
    """
    clone a git repository and launch a container instance in the
    requested OS chroot executing a build in the cloned source tree
    according to .travis-build.sh script (or <buildscript> if set)
    """
    NAME = "ci"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-B", "--build-config", metavar="file.yaml", action="store",
                            help="YAML file with build configuration")
        parser.add_argument("-a", "--artifacts", metavar="dir", action="store",
                            help="directory where build artifacts will be stored")
        parser.add_argument("--source-only", action="store_true",
                            help="only build source packages")
        parser.add_argument("--shell", action="store_true",
                            help="open a shell after the build")
        parser.add_argument("--linger", action="store_true",
                            help="do not shut down the container on exit")
        parser.add_argument("--option", "-O", action=BuildOptionAction,
                            help="key=value option for the build. See `-s list` for a list of"
                                 " available option for each build style")
        parser.add_argument("--quick", action="store_true",
                            help="quild quickly, assuming the container is up to date")
        parser.add_argument("system", action="store",
                            help="name or path of the system used to build")
        parser.add_argument("source", nargs="?", default=".",
                            help="path or url of the repository or source package to build."
                                 " Default: the current directory")
        return parser

    def run(self) -> None:
        # Defaults before loading YAML
        build_kwargs_system: dict[str, Any] = {
            "artifacts_dir": self.moncic.config.build_artifacts_dir,
            "quick": self.args.quick,
        }

        # Overrides after loading YAML
        build_kwargs_cmd: dict[str, Any] = {
            "source_only": self.args.source_only,
        }

        if self.args.artifacts:
            build_kwargs_cmd["artifacts_dir"] = os.path.abspath(self.args.artifacts)

        if self.args.option:
            build_kwargs_cmd.update(self.args.option)

        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                with self.source(system.distro, self.args.source) as source:
                    log.info("Source type: %s", source.get_name())

                    # Create a Build object with system-configured defaults
                    build = source.make_build(distro=system.distro, **build_kwargs_system)

                    # Load YAML configuration for the build
                    if self.args.build_config:
                        build.load_yaml(self.args.build_config)

                    # Update values with command line arguments
                    for k, v in build_kwargs_cmd.items():
                        setattr(build, k, v)

                    if build.artifacts_dir:
                        with self.moncic.privs.user():
                            os.makedirs(build.artifacts_dir, exist_ok=True)

                    builder = Builder(system, build)

                    if self.args.linger:
                        build.on_end.append("@linger")
                    if self.args.shell:
                        build.on_end.append("@shell")

                    try:
                        builder.run_build()
                    finally:
                        class ResultEncoder(json.JSONEncoder):
                            def default(self, obj):
                                if dataclasses.is_dataclass(obj):
                                    return dataclasses.asdict(obj)
                                elif isinstance(obj, InputSource):
                                    return obj.source
                                elif isinstance(obj, Distro):
                                    return obj.name
                                elif isinstance(obj, Path):
                                    return str(obj)
                                else:
                                    return super().default(obj)
                        json.dump(builder.build, sys.stdout, indent=1, cls=ResultEncoder)
                        sys.stdout.write("\n")


@main_command
class Lint(SourceCommand):
    """
    Run consistency checks on a source directory using all available build
    styles
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", action="store",
                            help="name or path of the system used for checking")
        parser.add_argument("source", nargs="?", default=".",
                            help="path or url of the repository or source package to check."
                                 " Default: the current directory")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                with self.source(system.distro, self.args.source) as source:
                    linter_cls = source.get_linter_class()
                    linter = linter_cls(system, source)
                    linter.lint()

        # cls.builders["debian"].analyze(analyzer)
        # cls.builders["rpm"].analyze(analyzer)
        # # TODO: check that NEWS.md version matches upstream version


@main_command
class QuerySource(SourceCommand):
    """
    Query informations about a source
    """
    NAME = "query-source"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", action="store",
                            help="name or path of the system used to query the package")
        parser.add_argument("source", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        return parser

    def run(self):
        result = {}
        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                with self.source(system.distro, self.args.source) as source:
                    builder = Builder(system)
                    result["distribution"] = system.distro.name
                    log.info("Query using builder %r", builder.__class__.__name__)
                    result["build-deps"] = builder.get_build_deps(source)

        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
