import contextlib
import http.server
import logging
import os
import socketserver
import subprocess
import tempfile
import threading
import unittest
from typing import override, ClassVar, Any
from collections.abc import Generator
from pathlib import Path


class GitRepo(contextlib.ExitStack):
    """
    Temporary git repository used for testing
    """

    def __init__(self, workdir: Path | None = None) -> None:
        super().__init__()
        if workdir is None:
            self.root = Path(self.enter_context(tempfile.TemporaryDirectory()))
        else:
            workdir.mkdir(parents=True, exist_ok=True)
            self.root = workdir

        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "hyde@example.com")

    def git(self, *args: str) -> None:
        """
        Run git commands in the test repository
        """
        cmd = ["git"]
        cmd.extend(args)
        subprocess.run(cmd, cwd=self.root, check=True, capture_output=True)

    def add(self, relpath: str, content: str | bytes = b"") -> None:
        """
        Create a file and git add it
        """
        dest = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        with open(dest, "wb") as out:
            if isinstance(content, str):
                out.write(content.encode())
            else:
                out.write(content)
        self.git("add", relpath)

    def commit(self, message: str = "test commit") -> None:
        """
        Run git commit with the given message
        """
        self.git("commit", "-m", message)

    @contextlib.contextmanager
    def serve(self) -> Generator[str]:
        """
        Run a webserver serving the repo contents for the duration of this
        context manager.

        The context variable will be the URL one can use with git to clone the
        repository
        """
        self.git("update-server-info")

        root = self.root

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["directory"] = root
                super().__init__(*args, **kwargs)

            @override
            def log_message(self, *args: Any) -> None:
                logging.debug(*args)

        # Auto-allocate the server port
        with socketserver.TCPServer(("localhost", 0), Handler) as httpd:
            port = httpd.server_address[1]

            server = threading.Thread(target=httpd.serve_forever, name="test-git-http-server")
            server.start()

            try:
                yield f"http://localhost:{port}/.git"
            finally:
                httpd.shutdown()
                server.join()


class WorkdirFixture(unittest.TestCase):
    stack: ClassVar[contextlib.ExitStack]
    workdir: Path

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # We have self.enterContext from Python 3.11
        cls.stack = contextlib.ExitStack()
        cls.stack.__enter__()
        cls.workdir = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))

    @override
    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.__exit__(None, None, None)
        super().tearDownClass()


class GitFixture(WorkdirFixture):
    path: Path
    git: GitRepo
    git_name: str = "repo"

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path = cls.workdir / cls.git_name
        cls.git = cls.stack.enter_context(GitRepo(cls.path))


def create_lint_version_fixture_path(path: Path) -> None:
    (path / "configure.ac").write_text("AC_INIT([test],[1.1],[enrico@enricozini.org]\n")
    (path / "meson.build").write_text("project('test', 'cpp', version: '1.2')\n")
    (path / "CMakeLists.txt").write_text('set(PACKAGE_VERSION "1.3")\n')
    (path / "NEWS.md").write_text("# New in version 1.4\n")
    (path / "setup.py").write_text(
        """
from setuptools import setup
setup(name='test', packages=['test'])
"""
    )
    (path / "test").mkdir()
    (path / "test" / "__init__.py").write_text('__version__ = "1.5"')
    (path / "setup.cfg").write_text("[metadata]\nversion = attr: test.__version__")
    (path / "debian").mkdir()
    (path / "debian" / "changelog").write_text("test (1.6-1) UNRELEASED; urgency=low")
    (path / "fedora" / "SPECS").mkdir(parents=True)
    (path / "fedora" / "SPECS" / "test.spec").write_text(
        """
Name:           test
Version:        1.6
Release:        1
Summary:        test repo
License:        CC-0
%description
test
"""
    )


def create_lint_version_fixture_git(
    git: GitRepo, *, upstream: bool = True, rpm: bool = True, debian: bool = True
) -> None:
    if upstream:
        git.add("configure.ac", "AC_INIT([test],[1.1],[enrico@enricozini.org]\n")
        git.add("meson.build", "project('test', 'cpp', version: '1.2')\n")
        git.add("CMakeLists.txt", 'set(PACKAGE_VERSION "1.3")\n')
        git.add("NEWS.md", "# New in version 1.4\n")
        git.add(
            "setup.py",
            """
from setuptools import setup
setup(name='test', packages=['test'])
""",
        )
        git.add("test/__init__.py", '__version__ = "1.5"')
        git.add("setup.cfg", "[metadata]\nversion = attr: test.__version__")
    if debian:
        git.add("debian/changelog", "test (1.6-1) UNRELEASED; urgency=low")
    if rpm:
        git.add(
            "fedora/SPECS/test.spec",
            """
Name:           test
Version:        1.6
Release:        1
Summary:        test repo
License:        CC-0
%description
test
""",
        )
