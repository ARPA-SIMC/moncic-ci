from __future__ import annotations

import dataclasses
import json
import logging
import sys
from pathlib import Path
from typing import Any

from ..build import Build
from ..distro import Distro
from ..operations import build as ops_build
from ..operations import query as ops_query
from ..source import Source
from ..source.lint import host_lint
from .moncic import SourceCommand, main_command
from .utils import BuildOptionAction, set_build_option_action

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
        parser.add_argument(
            "-B", "--build-config", metavar="file.yaml", action="store", help="YAML file with build configuration"
        )
        parser.add_argument(
            "-a",
            "--artifacts",
            metavar="dir",
            action="store",
            type=Path,
            help="directory where build artifacts will be stored",
        )
        parser.add_argument("--source-only", action="store_true", help="only build source packages")
        parser.add_argument("--shell", action="store_true", help="open a shell after the build")
        parser.add_argument("--linger", action="store_true", help="do not shut down the container on exit")
        parser.add_argument(
            "--option",
            "-O",
            action=BuildOptionAction,
            help="key=value option for the build. See `-O list` for a list of" " available option for each build style",
        )
        parser.add_argument("--quick", action="store_true", help="quild quickly, assuming the container is up to date")
        parser.add_argument("image", action="store", help="name of the image used to build")
        parser.add_argument(
            "source",
            nargs="?",
            default=".",
            help="path or url of the repository or source package to build." " Default: the current directory",
        )
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
            build_kwargs_cmd["artifacts_dir"] = self.args.artifacts.absolute()

        if self.args.option:
            build_kwargs_cmd.update(self.args.option)

        with self.moncic.session() as session:
            images = session.images
            image = images.image(self.args.image)
            with self.source(image.distro) as source:
                log.info("Source type: %s", source.__class__)

                # Create a Build object with system-configured defaults
                build_class = Build.get_build_class(source)
                build = build_class(source=source, distro=image.distro, **build_kwargs_system)

                # Load YAML configuration for the build
                if self.args.build_config:
                    build.load_yaml(self.args.build_config)

                # Update values with command line arguments
                for k, v in build_kwargs_cmd.items():
                    set_build_option_action(build, k, v)

                if build.artifacts_dir:
                    build.artifacts_dir.mkdir(parents=True, exist_ok=True)

                builder = ops_build.Builder(image, build)

                if self.args.linger:
                    build.on_end.append("@linger")
                if self.args.shell:
                    build.on_end.append("@shell")

                try:
                    builder.host_main()
                finally:

                    class ResultEncoder(json.JSONEncoder):
                        def default(self, obj):
                            if dataclasses.is_dataclass(obj):
                                return dataclasses.asdict(obj)
                            elif isinstance(obj, Source):
                                return obj.name
                            elif isinstance(obj, Distro):
                                return obj.name
                            elif isinstance(obj, Path):
                                return str(obj)
                            else:
                                return super().default(obj)

                    info = dataclasses.asdict(builder.build)
                    info["source_history"] = builder.build.source.info_history()
                    json.dump(info, sys.stdout, indent=1, cls=ResultEncoder)
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
        parser.add_argument("image", action="store", help="name of the image used for checking")
        parser.add_argument(
            "source",
            nargs="?",
            default=".",
            help="path or url of the repository or source package to check." " Default: the current directory",
        )
        return parser

    def run(self):
        from ..source.lint import Reporter

        reporter = Reporter()
        with self.local_source() as local_source:
            host_lint(local_source, reporter)

            with self.moncic.session() as session:
                images = session.images
                image = images.image(self.args.image)
                source = self.distro_source(local_source, image.distro)
                operation = ops_query.Lint(image, source, reporter=reporter)
                reporter = operation.host_main()
        if reporter.error_count:
            print(f"{reporter.error_count} error(s), {reporter.warning_count} warning(s)")
            return 2
        if reporter.warning_count:
            print(f"{reporter.warning_count} warning(s)")
            return 1


@main_command
class QuerySource(SourceCommand):
    """
    Query informations about a source
    """

    NAME = "query-source"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("image", action="store", help="name of the image used to query the package")
        parser.add_argument(
            "source",
            nargs="?",
            default=".",
            help="path or url of the repository to build. Default: the current directory",
        )
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            image = images.image(self.args.image)
            with self.source(image.distro) as source:
                operation = ops_query.Query(image, source)
                result = operation.host_main()
                # result["distribution"] = image.distro.name
                # result["build-deps"] = builder.get_build_deps(source)

        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
