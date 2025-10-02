import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path
from typing import Any, override

from moncic.distro import Distro
from moncic.exceptions import Fail
from moncic.image import RunnableImage
from moncic.operations import build as ops_build
from moncic.operations import query as ops_query
from moncic.source import Source
from moncic.source.lint import host_lint
from moncic.utils.script import Script

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

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
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
            if not isinstance(image, RunnableImage):
                raise Fail(f"image {image.name} has not been bootstrapped")
            with self.source(image.distro) as source:
                log.info("Source type: %s", source.__class__)

                # Get the builder class to use
                builder_class = ops_build.Builder.get_builder_class(source)

                # Fill in the build configuration
                config = builder_class.build_config_class(**build_kwargs_system)

                # Load YAML configuration for the build
                if self.args.build_config:
                    config.load_yaml(self.args.build_config)

                # Update values with command line arguments
                for k, v in build_kwargs_cmd.items():
                    set_build_option_action(config, k, v)

                if self.args.linger:
                    config.on_end.append("@linger")
                if self.args.shell:
                    config.on_end.append("@shell")

                if config.artifacts_dir:
                    config.artifacts_dir.mkdir(parents=True, exist_ok=True)

                with builder_class(source, image, config) as builder:
                    try:
                        builder.host_main()
                    finally:

                        class ResultEncoder(json.JSONEncoder):
                            @override
                            def default(self, obj: Any) -> Any:
                                if dataclasses.is_dataclass(obj):
                                    return dataclasses.asdict(obj)
                                elif isinstance(obj, Source):
                                    return obj.name
                                elif isinstance(obj, Distro):
                                    return obj.name
                                elif isinstance(obj, Path):
                                    return str(obj)
                                elif isinstance(obj, Script):
                                    res: dict[str, Any] = {
                                        "title": obj.title,
                                    }
                                    if obj.cwd:
                                        res["cwd"] = obj.cwd.as_posix()
                                    if obj.user:
                                        res["user"] = obj.user.user_name
                                    if obj.disable_network:
                                        res["disable_network"] = obj.disable_network
                                    res["shell"] = obj.shell
                                    res["lines"] = obj.lines
                                    return res
                                else:
                                    return super().default(obj)

                        info: dict[str, Any] = {}
                        info["config"] = dataclasses.asdict(builder.config)
                        info["source_history"] = builder.source.info_history()
                        info["result"] = dataclasses.asdict(builder.results)
                        json.dump(info, sys.stdout, indent=1, cls=ResultEncoder)
                        sys.stdout.write("\n")


@main_command
class Lint(SourceCommand):
    """
    Run consistency checks on a source directory using all available build
    styles
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument("image", action="store", help="name of the image used for checking")
        parser.add_argument(
            "source",
            nargs="?",
            default=".",
            help="path or url of the repository or source package to check." " Default: the current directory",
        )
        return parser

    def run(self) -> int | None:
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
        return None


@main_command
class QuerySource(SourceCommand):
    """
    Query informations about a source
    """

    NAME = "query-source"

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument("image", action="store", help="name of the image used to query the package")
        parser.add_argument(
            "source",
            nargs="?",
            default=".",
            help="path or url of the repository to build. Default: the current directory",
        )
        return parser

    def run(self) -> None:
        with self.moncic.session() as session:
            images = session.images
            image = images.image(self.args.image)
            if not isinstance(image, RunnableImage):
                raise Fail(f"image {image.name} has not been bootstrapped")
            with self.source(image.distro) as source:
                operation = ops_query.Query(image, source)
                result = operation.host_main()
                # result["distribution"] = image.distro.name
                # result["build-deps"] = builder.get_build_deps(source)

        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
