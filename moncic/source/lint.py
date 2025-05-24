from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, override

from .local import Git, LocalSource

if TYPE_CHECKING:
    from .source import Source

log = logging.getLogger(__name__)


class Reporter:
    """
    Report lint result
    """

    def __init__(self) -> None:
        #: Number of errors found
        self.error_count: int = 0
        #: Number of warnings found
        self.warning_count: int = 0

    def error(self, source: Source, message: str) -> None:
        log.error("%s", message)
        self.error_count += 1

    def warning(self, source: Source, message: str) -> None:
        log.warning("%s", message)
        self.warning_count += 1


class BaseLinter:
    """
    Run consistency checks on sources
    """

    versions: dict[str, str]

    def __init__(self, source: LocalSource, reporter: Reporter) -> None:
        self.source = source
        self.reporter = reporter

    def check_versions(self) -> None:
        by_version: dict[str, list[str]] = defaultdict(list)
        for name, version in self.versions.items():
            if name.endswith("-release"):
                by_version[version.split("-", 1)[0]].append(name)
            else:
                by_version[version].append(name)
        if len(by_version) > 1:
            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
            self.reporter.warning(self.source, f"Versions mismatch: {'; '.join(descs)}")

    def check_branches(self) -> None:
        assert isinstance(self.source, Git)

        upstream = self.source.lint_find_upstream_tag()
        if upstream is None:
            self.reporter.warning(self.source, "upstream tag not found")

        packaging = self.source.lint_find_packaging_tag()
        if packaging is None:
            packaging = self.source.lint_find_packaging_branch()

        if upstream is not None and packaging is not None:
            log.info("Checking changes from upstream %s to packaging %s", upstream, packaging)

            # Check if the packaging branch introduced changes to the upstream sources
            upstream_affected: set[str] = set()
            for diff in upstream.commit.diff(packaging.commit):
                if diff.a_path is not None and not self.source.lint_path_is_packaging(Path(diff.a_path)):
                    upstream_affected.add(diff.a_path)
                if diff.b_path is not None and not self.source.lint_path_is_packaging(Path(diff.b_path)):
                    upstream_affected.add(diff.b_path)
            for name in sorted(upstream_affected):
                self.reporter.warning(self.source, f"{name}: upstream file affected by debian branch")

    def lint_path_is_packaging(self, path: Path) -> bool:
        """
        Check if a path looks like packaging instead of upstream
        """
        return False

    def run(self) -> None:
        pass


class HostLinter(BaseLinter):
    def __init__(self, source: LocalSource, reporter: Reporter) -> None:
        super().__init__(source, reporter)
        self.versions = source.lint_find_versions(allow_exec=False)


class GuestLinter(BaseLinter):
    def __init__(self, source: LocalSource, reporter: Reporter) -> None:
        super().__init__(source, reporter)
        self.versions = source.lint_find_versions(allow_exec=True)

    @override
    def run(self) -> None:
        super().run()
        self.check_versions()
        if isinstance(self.source, Git):
            self.check_branches()


def host_lint(source: LocalSource, reporter: Reporter) -> None:
    """
    Perform consistency checks on the source in the host system.

    This cannot assume any distro-specific tools to be available.

    This can assume access to the original sources, unless they are remote.
    """
    linter = HostLinter(source, reporter)
    linter.run()


def guest_lint(source: LocalSource, reporter: Reporter) -> None:
    """
    Perform consistency checks on the source in the guest system.

    This can assume distro-specific tools to be available.

    This cannot assume access to the original sources.
    """
    linter = GuestLinter(source, reporter)
    linter.run()


# from __future__ import annotations
#
# import re
# from collections import defaultdict
# from functools import cached_property
# from pathlib import Path
# from typing import TYPE_CHECKING
#
# import git
#
# if TYPE_CHECKING:
#     from ..container import System
#     from ..source.source import Source
#
#
# class Linter:
#     """
#     Scan sources for potential inconsistencies
#     """
#
#     def __init__(self, system: System, source: Source):
#         super().__init__()
#         # System used to analyze the sources
#         self.system = system
#         # Source to check
#         self.source = source
#         # Number of errors found
#         self.error_count: int = 0
#         # Number of warnings found
#         self.warning_count: int = 0
#
#     def error(self, message: str):
#         print(message)
#         self.error_count += 1
#
#     def warning(self, message: str):
#         print(message)
#         self.warning_count += 1
#
#     @cached_property
#     def source_path(self) -> Path:
#         """
#         Get the source path
#         """
#         return self.source.host_path
#
#     @cached_property
#     def main_branch(self) -> str:
#         """
#         Find the main branch name
#         """
#         for name in "main", "master":
#             if name not in self.repo.branches:
#                 continue
#             self.check_local_remote_sync(name)
#             return name
#         return "main"
#
#     @cached_property
#     def debian_packaging_branches(self) -> dict[str, str]:
#         """
#         List Debian/Ubuntu packaging branches found.
#
#         Returns a dict mapping branch names to their most up to date version
#         """
#         tags = {x.name for x in self.repo.tags if x.name.split("/", 1)[0] in ("debian", "ubuntu")}
#
#         local_branches = set()
#         remote_branches = set()
#         for x in self.repo.references:
#             if x.name in tags:
#                 continue
#             for distro in "debian", "ubuntu":
#                 if x.name.startswith(f"origin/{distro}/"):
#                     remote_branches.add(x.name[7:])
#                 elif x.name.startswith(f"{distro}/"):
#                     local_branches.add(x.name)
#
#         res: dict[str, str] = {}
#
#         for name in local_branches - remote_branches:
#             self.error(f"branch {name!r} exists locally but not in origin")
#             res[name] = name
#
#         for name in remote_branches - local_branches:
#             self.warning(f"branch {name!r} exists in origin but not locally")
#             res[name] = "origin/" + name
#
#         for name in local_branches & remote_branches:
#             res[name] = self.check_local_remote_sync(name)
#
#         return res
#
#     @cached_property
#     def version_from_debian_branches(self) -> dict[str, str]:
#         """
#         Get the debian version from Debian branches
#
#         Return a dict mapping version type to version
#         """
#         re_changelog = re.compile(r"\S+\s+\(([^)]+)\)")
#         versions: dict[str, str] = {}
#         to_check = list(self.debian_packaging_branches.items())
#         to_check.append((self.main_branch, self.main_branch))
#         for name, branch_name in to_check:
#             branch = self.repo.references[branch_name]
#             if "debian" not in branch.commit.tree:
#                 continue
#             changelog = branch.commit.tree["debian"]["changelog"]
#             for line in changelog.data_stream.read().decode().splitlines():
#                 if mo := re_changelog.match(line):
#                     versions[name] = mo.group(1)
#                     break
#
#         # Check for mismatches
#         by_version: dict[str, list[str]] = defaultdict(list)
#         for name, version in versions.items():
#             by_version[version].append(name)
#         if len(by_version) > 1:
#             descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
#             self.warning(f"Versions mismatch: {'; '.join(descs)}")
#
#         return versions
#
#     @classmethod
#     def same_values(cls, versions: dict[str, str]) -> str | None:
#         """
#         If all the dict's entries have the same value, return that value.
#
#         Else, return None
#         """
#         res = set(versions.values())
#         if len(res) == 1:
#             return next(iter(res))
#         else:
#             return None
#
# class ARPALinter(Linter):
#     def lint(self):
#         super().lint()
#         # # Check that spec version is in sync with upstream
#         # upstream_version = Analyzer.same_values(analyzer.version_from_sources)
#         # spec_version = analyzer.version_from_arpa_specfile
#         # if upstream_version and upstream_version != spec_version:
#         #     analyzer.warning(f"Upstream version {upstream_version!r} is different than specfile {spec_version!r}")
#
#         # TODO: check that upstream tag exists
#
# class DebianLinter(Linter):
#     def lint(self):
#         super().lint()
#         # upstream_version = analyzer.upstream_version
#         # debian_version = Analyzer.same_values(analyzer.version_from_debian_branches)
#
#         # # Check that debian/changelog versions are in sync with upstream
#         # if upstream_version is not None and debian_version is not None:
#         #     if upstream_version not in debian_version:
#         #         analyzer.warning(f"Debian version {debian_version!r} is out of sync"
#         #                          f" with upstream version {upstream_version!r}")
#         #     # if debian_version is None:
#         #     #     analyzer.warning("Cannot univocally determine debian version")
#
#         # # Check upstream merge status of the various debian branches
#         # upstream_branch = analyzer.repo.references[analyzer.main_branch]
#         # for name, branch_name in analyzer.debian_packaging_branches.items():
#         #     debian_branch = analyzer.repo.references[branch_name]
#         #     if not analyzer.repo.is_ancestor(upstream_branch, debian_branch):
#         #         analyzer.warning(f"Upstream branch {analyzer.main_branch!r} is not merged in {name!r}")
#
#         # TODO: check tags present for one distro but not for the other
#         # TODO: check that upstream tag exists if debian/changelog is not UNRELEASED
