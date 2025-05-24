import unittest
from pathlib import Path
from typing import override, Self
from unittest import mock

from moncic.distro import DistroFamily, Distro
from moncic.source import Source
from moncic.source.distro import DistroSource
from moncic.source.lint import Reporter, guest_lint
from moncic.source.local import Dir, File, Git


ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class MockDistroSource(DistroSource):
    @override
    @classmethod
    def create_from_file(cls, parent: File, *, distro: Distro) -> DistroSource:
        raise NotImplementedError()

    @override
    @classmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro) -> DistroSource:
        raise NotImplementedError()

    @override
    @classmethod
    def create_from_git(cls, parent: Git, *, distro: Distro) -> DistroSource:
        raise NotImplementedError()

    @override
    def in_path(self, path: Path) -> Self:
        raise NotImplementedError()


class MockReporter(Reporter):
    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @override
    def error(self, source: Source, message: str) -> None:
        self.errors.append(message)
        self.error_count += 1

    @override
    def warning(self, source: Source, message: str) -> None:
        self.warnings.append(message)
        self.warning_count += 1


class TestLint(unittest.TestCase):
    def assertLint(
        self, *, versions: dict[str, str], errors: list[str] | None = None, warnings: list[str] | None = None
    ) -> None:
        """
        Make sure we get the given lint warning given a set of detected versions
        """
        reporter = MockReporter()
        source = MockDistroSource(name="test", path=Path("/dev/null"), distro=SID)

        with mock.patch("moncic.source.local.LocalSource.lint_find_versions", return_value=versions):
            guest_lint(source, reporter)

        if errors:
            self.assertEqual(reporter.errors, errors)
        else:
            self.assertEqual(reporter.errors, [])

        if warnings:
            self.assertEqual(reporter.warnings, warnings)
        else:
            self.assertEqual(reporter.warnings, [])

    def test_versions(self) -> None:
        self.assertLint(
            versions={
                "autotools": "1.1",
                "meson": "1.2",
                "debian-release": "1.2-1",
                "spec-release": "1.2-1rocky9",
            },
            warnings=["Versions mismatch: 1.1 in autotools; 1.2 in meson, debian-release, spec-release"],
        )

    def test_versions_tag(self) -> None:
        self.assertLint(
            versions={
                "autotools": "1.2",
                "meson": "1.2",
                "tag": "1.3",
                "debian-release": "1.2-1",
                "spec-release": "1.2-1rocky9",
            },
            warnings=["Versions mismatch: 1.2 in autotools, meson, debian-release, spec-release; 1.3 in tag"],
        )

    def test_versions_tag_debian(self) -> None:
        self.assertLint(
            versions={
                "autotools": "1.2",
                "meson": "1.2",
                "tag-debian": "1.3",
                "tag-debian-release": "1.3-1",
            },
            warnings=["Versions mismatch: 1.2 in autotools, meson; 1.3 in tag-debian, tag-debian-release"],
        )

    def test_versions_tag_arpa(self) -> None:
        self.assertLint(
            versions={
                "autotools": "1.2",
                "meson": "1.2",
                "tag-arpa": "1.3",
                "tag-arpa-release": "1.3-1",
            },
            warnings=["Versions mismatch: 1.2 in autotools, meson; 1.3 in tag-arpa, tag-arpa-release"],
        )


# class TestGit(unittest.TestCase):
#     def setUp(self):
#         super().setUp()
#         self.method_stack = contextlib.ExitStack()
#         self.method_stack.__enter__()
#         self.moncic = self.method_stack.enter_context(make_moncic())
#         self.session = self.method_stack.enter_context(self.moncic.mock_session())
#         self.repo = GitRepo()
#         self.repo.add("meson.build", "project('test', 'cpp', version: '1.2')\n")
#         self.repo.commit("initial")
#         self.repo.git("tag", "v1.2")
#
#     def _find_versions(self, dist: Distro) -> dict[str, str]:
#         with InputSource.create(self.repo.root) as isrc:
#             src = isrc.detect_source(dist)
#             linter_cls = src.get_linter_class()
#             with self.session.images.system(dist.name) as system:
#                 linter = linter_cls(system, src)
#                 return linter.find_versions()
#
#     def _lint(self, dist: Distro) -> tuple[list[str], list[str]]:
#         with InputSource.create(self.repo.root) as isrc:
#             src = isrc.detect_source(dist)
#             linter_cls = src.get_linter_class()
#             with self.session.images.system(dist.name) as system:
#                 linter = linter_cls(system, src)
#                 warnings: list[str] = []
#                 errors: list[str] = []
#                 linter.warning = lambda msg: warnings.append(msg)
#                 linter.error = lambda msg: errors.append(msg)
#                 linter.lint()
#                 return warnings, errors
#
#     def test_tag_version(self):
#         self.repo.git("checkout", "-b", "debian/sid")
#         self.repo.add("debian/changelog", "test (1.1-1) UNRELEASED; urgency=low")
#         self.repo.commit("packaged for debian")
#         self.repo.git("tag", "debian/1.2-1")
#
#         self.repo.git("checkout", "main")
#         self.repo.add(
#             "fedora/SPECS/test.spec",
#             """
# %global releaseno 1
# Name:           test
# Version:        1.2
# Release:        %{releaseno}%{dist}
# """,
#         )
#         self.repo.commit("packaged for fedora/ARPA")
#         self.repo.git("tag", "v1.2-1")
#
#         self.repo.git("checkout", "debian/sid")
#         versions = self._find_versions(SID)
#         self.assertEqual(
#             versions,
#             {
#                 "debian-release": "1.1-1",
#                 "debian-upstream": "1.1",
#                 "meson": "1.2",
#                 "tag-debian": "1.2",
#                 "tag-debian-release": "1.2-1",
#             },
#         )
#
#         self.repo.git("checkout", "main")
#         self.session.set_process_result(
#             r"rpmspec",
#             stdout=b"""
# Version: 1.2
# Release: 2rocky9
# """,
#         )
#         versions = self._find_versions(ROCKY9)
#         self.assertEqual(
#             versions,
#             {
#                 "meson": "1.2",
#                 "tag-arpa": "1.2",
#                 "tag-arpa-release": "1.2-1",
#                 "spec-release": "1.2-2rocky9",
#                 "spec-upstream": "1.2",
#             },
#         )
#
#     def test_packaging_changes_deb_test(self):
#         # Consistency between tags and commit history: the changes between an
#         # upstream tag and a packaging tag/branch should only affect the
#         # packaging files
#         self.repo.git("checkout", "-b", "debian/sid")
#         self.repo.add("debian/changelog", "test (1.2-1) UNRELEASED; urgency=low")
#         self.repo.add(
#             "debian/gbp.conf",
#             """
# [DEFAULT]
# upstream-branch=main
# upstream-tag=%(version)s
# debian-branch=debian/sid
# """,
#         )
#         self.repo.commit("packaged for debian")
#         self.assertEqual(self._lint(SID), ([], []))
#
#         self.repo.add("file", "contents")
#         self.repo.commit("change to upstream")
#         self.assertEqual(self._lint(SID), (["file: upstream file affected by debian branch"], []))
#
#     def test_packaging_changes_deb_release(self):
#         # Consistency between tags and commit history: the changes between an
#         # upstream tag and a packaging tag/branch should only affect the
#         # packaging files
#         self.repo.git("checkout", "-b", "debian/sid")
#         self.repo.add("debian/changelog", "test (1.2-1) UNRELEASED; urgency=low")
#         self.repo.add(
#             "debian/gbp.conf",
#             """
# [DEFAULT]
# upstream-branch=main
# upstream-tag=%(version)s
# debian-branch=debian/sid
# """,
#         )
#         self.repo.commit("packaged for debian")
#         self.assertEqual(self._lint(SID), ([], []))
#
#         self.repo.git("tag", "debian/1.2-1")
#         self.assertEqual(self._lint(SID), ([], []))
#
#         self.repo.add("file", "contents")
#         self.repo.commit("change to upstream")
#
#         self.repo.add("debian/changelog", "test (1.2-2) UNRELEASED; urgency=low")
#         self.repo.commit("packaged")
#
#         self.repo.git("tag", "debian/1.2-2")
#         self.assertEqual(self._lint(SID), (["file: upstream file affected by debian branch"], []))
#
#     def test_packaging_changes_arpa(self):
#         self.repo.add(
#             "fedora/SPECS/test.spec",
#             """
# %global releaseno 1
# Name:           test
# Version:        1.2
# Release:        %{releaseno}%{dist}
# """,
#         )
#         self.repo.commit("packaged for fedora/ARPA")
#         self.repo.git("tag", "v1.2-1")
#         self.session.set_process_result(
#             r"rpmspec",
#             stdout=b"""
# Version: 1.2
# Release: 1rocky9
# """,
#         )
#         self.assertEqual(self._lint(ROCKY9), ([], []))
#
#         self.repo.add("file", "contents")
#         self.repo.commit("change to upstream")
#         self.session.set_process_result(
#             r"rpmspec",
#             stdout=b"""
# Version: 1.2
# Release: 1rocky9
# """,
#         )
#         self.assertEqual(self._lint(ROCKY9), ([], []))
#
#         self.repo.git("tag", "v1.2-2")
#         self.session.set_process_result(
#             r"rpmspec",
#             stdout=b"""
# Version: 1.2
# Release: 2rocky9
# """,
#         )
#         self.assertEqual(self._lint(ROCKY9), (["file: upstream file affected by packaging changes"], []))
#
#         # ------------
#         # TODO: Checks in git history:
#
#         # Consistency between tag and version/release in the spec file: a
#         # specific tag must be consistent with the Version and Release fields
#         # set in the spec file.
