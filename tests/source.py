from __future__ import annotations

import contextlib
import http.server
import logging
import os
import socketserver
import subprocess
import tempfile
import threading
from typing import Generator, Union

from moncic.distro import Distro, DistroFamily


class MockSystem:
    def __init__(self, distro: Distro):
        self.distro = distro


class MockBuilder(contextlib.ExitStack):
    def __init__(self, distro: str):
        super().__init__()
        self.system = MockSystem(
                distro=DistroFamily.lookup_distro(distro))


class GitRepo(contextlib.ExitStack):
    """
    Temporary git repository used for testing
    """
    def __init__(self):
        super().__init__()
        self.root = self.enter_context(tempfile.TemporaryDirectory())
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

    def add(self, relpath: str, content: Union[str, bytes] = b''):
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
