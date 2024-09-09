from __future__ import annotations

import contextlib
import http.server
import logging
import os
import socketserver
import subprocess
import tempfile
import threading
import unittest
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.build import Build
from moncic.distro import Distro, DistroFamily

if TYPE_CHECKING:
    from moncic.source import Source


class MockSystem:
    def __init__(self, distro: Distro):
        self.distro = distro


class MockContainer:
    def __init__(self, system: MockSystem, root: str):
        self.system = system
        self.root = root
        self.source_dir = os.path.join(self.root, "srv", "moncic-ci", "source")
        os.makedirs(self.source_dir, exist_ok=True)

    def get_root(self):
        return self.root


class MockBuilder(contextlib.ExitStack):
    def __init__(self, distro: str, build: Build):
        super().__init__()
        self.system = MockSystem(distro=DistroFamily.lookup_distro(distro))
        self.build = build

    def setup_build(self, *, source: Source, **kw):
        self.build = Build(source=source, **kw)

    @contextlib.contextmanager
    def container(self) -> Generator[MockContainer, None, None]:
        with tempfile.TemporaryDirectory() as container_root:
            yield MockContainer(self.system, container_root)


class GitRepo(contextlib.ExitStack):
    """
    Temporary git repository used for testing
    """

    def __init__(self, workdir: Path | None = None):
        super().__init__()
        if workdir is None:
            self.root = Path(self.enter_context(tempfile.TemporaryDirectory()))
        else:
            workdir.mkdir(parents=True, exist_ok=True)
            self.root = workdir

        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "hyde@example.com")

    def git(self, *args: str):
        """
        Run git commands in the test repository
        """
        cmd = ["git"]
        cmd.extend(args)
        subprocess.run(cmd, cwd=self.root, check=True, capture_output=True)

    def add(self, relpath: str, content: str | bytes = b""):
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

    def commit(self, message="test commit"):
        """
        Run git commit with the given message
        """
        self.git("commit", "-m", message)

    @contextlib.contextmanager
    def serve(self) -> Generator[str, None, None]:
        """
        Run a webserver serving the repo contents for the duration of this
        context manager.

        The context variable will be the URL one can use with git to clone the
        repository
        """
        self.git("update-server-info")

        root = self.root

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kw):
                kw["directory"] = root
                super().__init__(*args, **kw)

            def log_message(self, *args):
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
    workdir: Path

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # We have self.enterContext from Python 3.11
        cls.stack = contextlib.ExitStack()
        cls.stack.__enter__()
        cls.workdir = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))

    @classmethod
    def tearDownClass(cls):
        cls.stack.__exit__(None, None, None)
        super().tearDownClass()


class GitFixture(WorkdirFixture):
    path: Path
    git: GitRepo
    git_name: str = "repo"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.workdir / cls.git_name
        cls.git = cls.stack.enter_context(GitRepo(cls.path))
