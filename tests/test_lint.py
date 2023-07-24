from __future__ import annotations

import contextlib
import unittest
from unittest import mock

from moncic.distro import DistroFamily
from moncic.lint import Linter
from moncic.source import InputSource
from moncic.unittest import make_moncic

from .source import GitFixtureMixin, WorkdirFixtureMixin

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

        # Consistency between tag and version/release in the spec file: a
        # specific tag must be consistent with the Version and Release fields
        # set in the spec file.

        # Consistency between tags and commit history: the changes between two
        # releases of the same upstream version (e.g. v${VERSION}-${RELEASE}
        # and v${VERSION}-${RELEASE-1} should affect only the package files
        # (spec, patch)


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


class TestLint(unittest.TestCase):
    def test_versions(self):
        linter = Linter(None, None)
        versions = {
            "autotools": "1.1",
            "meson": "1.2",
            'debian-release': '1.2-1',
            'spec-release': '1.2-1rocky9',
        }
        with (mock.patch("moncic.lint.Linter.find_versions", return_value=versions),
              mock.patch("moncic.lint.Linter.warning") as warnings):
            linter.lint()
            warnings.assert_called_with("Versions mismatch: 1.1 in autotools; 1.2 in meson")
