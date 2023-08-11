from __future__ import annotations

import contextlib
import unittest
from typing import TYPE_CHECKING
from unittest import mock

from moncic.distro import DistroFamily
from moncic.lint import Linter
from moncic.source import InputSource, Source
from moncic.unittest import make_moncic

from .source import GitRepo, WorkdirFixtureMixin

if TYPE_CHECKING:
    from moncic.distro import Distro


ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class FindVersionsCommon(WorkdirFixtureMixin):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.workdir / "configure.ac").write_text("AC_INIT([test],[1.1],[enrico@enricozini.org]\n")
        (cls.workdir / "meson.build").write_text("project('test', 'cpp', version: '1.2')\n")
        (cls.workdir / "CMakeLists.txt").write_text('set(PACKAGE_VERSION "1.3")\n')
        (cls.workdir / "NEWS.md").write_text("# New in version 1.4\n")
        (cls.workdir / "setup.py").write_text("""
from setuptools import setup
setup(name='test', packages=['test'])
""")
        (cls.workdir / "test").mkdir()
        (cls.workdir / "test" / "__init__.py").write_text('__version__ = "1.5"')

    def setUp(self):
        super().setUp()
        self.method_stack = contextlib.ExitStack()
        self.method_stack.__enter__()
        self.moncic = self.method_stack.enter_context(make_moncic())
        self.session = self.method_stack.enter_context(self.moncic.mock_session())

    def tearDown(self):
        self.method_stack.__exit__(None, None, None)
        super().tearDown()

    def _find_versions(self, dist) -> dict[str, str]:
        with InputSource.create(self.workdir) as isrc:
            src = isrc.detect_source(dist)
            linter_cls = src.get_linter_class()
            with self.session.images.system(dist.name) as system:
                linter = linter_cls(system, src)
                self.session.set_process_result(r"python3 setup\.py", stdout=b"1.5\n")
                return linter.find_versions()


class TestFindVersionsDebian(FindVersionsCommon, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.workdir / "debian").mkdir()
        (cls.workdir / "debian" / "changelog").write_text("test (1.6-1) UNRELEASED; urgency=low")

    def test_find_versions(self):
        self.assertEqual(self._find_versions(SID), {
            'autotools': '1.1',
            'meson': '1.2',
            'cmake': '1.3',
            'news': '1.4',
            'setup.py': '1.5',
            'debian-upstream': '1.6',
            'debian-release': '1.6-1',
        })


class TestFindVersionsARPA(FindVersionsCommon, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.workdir / "fedora" / "SPECS").mkdir(parents=True)
        (cls.workdir / "fedora" / "SPECS" / "test.spec").write_text("""
%global releaseno 1
Name:           test
Version:        1.6
Release:        %{releaseno}%{dist}
""")

    def test_find_versions(self):
        self.session.set_process_result(r"rpmspec", stdout=b"""
Version: 1.6
Release: 1rocky9
""")
        self.assertEqual(self._find_versions(ROCKY9), {
            'autotools': '1.1',
            'meson': '1.2',
            'cmake': '1.3',
            'news': '1.4',
            'setup.py': '1.5',
            'spec-upstream': '1.6',
            'spec-release': '1.6-1rocky9',
        })


class TestVersions(unittest.TestCase):
    def assertWarns(self, versions: dict[str, str], message: str):
        with mock.patch("moncic.source.Source.__post_init__"):
            source = Source(None, None, None)
        linter = Linter(None, source)
        with (mock.patch("moncic.source.Source.find_versions", return_value=versions),
              mock.patch("moncic.lint.Linter.warning") as warnings):
            linter.lint()
            warnings.assert_called_with(message)

    def test_versions(self):
        self.assertWarns({
            "autotools": "1.1",
            "meson": "1.2",
            'debian-release': '1.2-1',
            'spec-release': '1.2-1rocky9',
        }, "Versions mismatch: 1.1 in autotools; 1.2 in meson, debian-release, spec-release")

    def test_versions_tag(self):
        self.assertWarns({
            "autotools": "1.2",
            "meson": "1.2",
            "tag": "1.3",
            'debian-release': '1.2-1',
            'spec-release': '1.2-1rocky9',
        }, "Versions mismatch: 1.2 in autotools, meson, debian-release, spec-release; 1.3 in tag")

    def test_versions_tag_debian(self):
        self.assertWarns({
            "autotools": "1.2",
            "meson": "1.2",
            'tag-debian': '1.3',
            'tag-debian-release': '1.3-1',
        }, "Versions mismatch: 1.2 in autotools, meson; 1.3 in tag-debian, tag-debian-release")

    def test_versions_tag_arpa(self):
        self.assertWarns({
            "autotools": "1.2",
            "meson": "1.2",
            'tag-arpa': '1.3',
            'tag-arpa-release': '1.3-1',
        }, "Versions mismatch: 1.2 in autotools, meson; 1.3 in tag-arpa, tag-arpa-release")


class TestGit(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.method_stack = contextlib.ExitStack()
        self.method_stack.__enter__()
        self.moncic = self.method_stack.enter_context(make_moncic())
        self.session = self.method_stack.enter_context(self.moncic.mock_session())

    def _find_versions(self, gitrepo: GitRepo, dist: Distro) -> dict[str, str]:
        with InputSource.create(gitrepo.root) as isrc:
            src = isrc.detect_source(dist)
            linter_cls = src.get_linter_class()
            with self.session.images.system(dist.name) as system:
                linter = linter_cls(system, src)
                return linter.find_versions()

    def test_tag_version(self):
        repo = GitRepo()
        repo.add("meson.build", "project('test', 'cpp', version: '1.2')\n")
        repo.commit("initial")
        repo.git("tag", "v1.2")

        repo.git("checkout", "-b", "debian/sid")
        repo.add("debian/changelog", "test (1.1-1) UNRELEASED; urgency=low")
        repo.commit("packaged for debian")
        repo.git("tag", "debian/1.2-1")

        repo.git("checkout", "main")
        repo.add("fedora/SPECS/test.spec", """
%global releaseno 1
Name:           test
Version:        1.2
Release:        %{releaseno}%{dist}
""")
        repo.commit("packaged for fedora/ARPA")
        repo.git("tag", "v1.2-1")

        repo.git("checkout", "debian/sid")
        versions = self._find_versions(repo, SID)
        self.assertEqual(versions, {
            "debian-release": "1.1-1",
            "debian-upstream": "1.1",
            "meson": "1.2",
            "tag-debian": "1.2",
            "tag-debian-release": "1.2-1",
        })

        repo.git("checkout", "main")
        self.session.set_process_result(r"rpmspec", stdout=b"""
Version: 1.2
Release: 2rocky9
""")
        versions = self._find_versions(repo, ROCKY9)
        self.assertEqual(versions, {
            "meson": "1.2",
            "tag-arpa": "1.2",
            "tag-arpa-release": "1.2-1",
            'spec-release': '1.2-2rocky9',
            'spec-upstream': '1.2',
        })

        # Checks in git history:

        # Consistency between tag and version/release in the spec file: a
        # specific tag must be consistent with the Version and Release fields
        # set in the spec file.

        # Consistency between tags and commit history: the changes between two
        # releases of the same upstream version (e.g. v${VERSION}-${RELEASE}
        # and v${VERSION}-${RELEASE-1} should affect only the package files
        # (spec, patch)
