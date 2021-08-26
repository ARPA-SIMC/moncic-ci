from __future__ import annotations
from typing import List, Dict, Any
import contextlib
import logging
import asyncio
import shlex
import os

log = logging.getLogger(__name__)


class RunFailed(Exception):
    """
    Exception raised when a task failed to run on the machine
    """
    pass


class AsyncioRunner:
    def __init__(self, cmd: List[str]):
        self.cmd = cmd
        self.stdout: List[bytes] = []
        self.stderr: List[bytes] = []

    def run(self):
        return asyncio.run(self._run())

    async def _run(self) -> Dict[str, Any]:
        log.info("Running %s", " ".join(shlex.quote(c) for c in self.cmd))

        proc = await asyncio.create_subprocess_exec(
                self.cmd[0], *self.cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)

        await asyncio.gather(
            self.read_stdout(proc.stdout),
            self.read_stderr(proc.stderr),
            proc.wait(),
        )

        if proc.returncode != 0:
            raise RunFailed(f"Run script exited with code {proc.returncode}")

        return {
            "stdout": b"".join(self.stdout),
            "stderr": b"".join(self.stderr),
            "returncode": proc.returncode,
        }

    async def read_stdout(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stdout.append(line)
            log.info("stdout: %s", line.decode(errors="replace").rstrip())

    async def read_stderr(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stderr.append(line)
            log.info("stderr: %s", line.decode(errors="replace").rstrip())


class LegacyRunner:
    def __init__(self, machine_name: str, workdir: str, cmd: List[str]):
        self.machine_name = machine_name
        self.workdir = workdir
        self.cmd = cmd
        self.fifo_stdout = os.path.join(self.workdir, "stdout")
        self.fifo_stderr = os.path.join(self.workdir, "stderr")
        self.fifo_result = os.path.join(self.workdir, "result")
        self.run_script = os.path.join(self.workdir, "script")

    def run(self):
        log.info("Running %s", " ".join(shlex.quote(c) for c in self.cmd))
        os.mkfifo(self.fifo_stdout)
        os.mkfifo(self.fifo_stderr)
        os.mkfifo(self.fifo_result)

        try:
            script = [
                "#!/bin/sh",
                (" ".join(shlex.quote(c) for c in self.cmd) +
                 " > /root/transfer/stdout 2> /root/transfer/stderr"),
                "echo $? > /root/transfer/result",
            ]

            with open(self.run_script, "wt") as fd:
                for line in script:
                    print(line, file=fd)

            cmd = [
                "systemd-run", "--quiet", "--setenv=HOME=/root",
                f"--machine={self.machine_name}", "--", "/bin/sh", "/root/transfer/script"
            ]

            return asyncio.run(self._run(cmd))
        finally:
            os.unlink(self.fifo_stdout)
            os.unlink(self.fifo_stderr)
            os.unlink(self.fifo_result)

    async def _run(self, cmd: List[str]):
        proc = await asyncio.create_subprocess_exec(cmd[0], *cmd[1:])

        stdout, stderr, returncode, proc_returncode = await asyncio.gather(
            self.read_stdout(),
            self.read_stderr(),
            self.read_result(),
            proc.wait(),
        )

        if returncode != 0:
            raise RunFailed(f"Run script exited with code {returncode}")

        if proc.returncode != 0:
            raise RunFailed(f"Executor command exited with code {proc.returncode}")

        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
        }

    @contextlib.asynccontextmanager
    async def open_fifo(self, fname: str):
        loop = asyncio.get_running_loop()
        try:
            # From https://gist.github.com/oconnor663/08c081904264043e55bf
            os_fd = os.open(fname, os.O_RDONLY | os.O_NONBLOCK)
            fd = os.fdopen(os_fd)
            reader = asyncio.StreamReader()
            read_protocol = asyncio.StreamReaderProtocol(reader)
            read_transport, _ = await loop.connect_read_pipe(
                lambda: read_protocol, fd)

            yield reader
        finally:
            fd.close()

    async def read_stdout(self):
        stdout = []
        async with self.open_fifo(self.fifo_stdout) as reader:
            while True:
                line = await reader.readline()
                if not line:
                    break
                stdout.append(line)
                log.info("stdout: %s", line.decode(errors="replace").rstrip())
        return b"".join(stdout)

    async def read_stderr(self):
        stderr = []
        async with self.open_fifo(self.fifo_stderr) as reader:
            while True:
                line = await reader.readline()
                if not line:
                    break
                stderr.append(line)
                log.info("stderr: %s", line.decode(errors="replace").rstrip())

        return b"".join(stderr)

    async def read_result(self):
        result = []
        async with self.open_fifo(self.fifo_result) as reader:
            while True:
                line = await reader.readline()
                if not line:
                    break
                result.append(line)
                log.info("result: %s", line.decode(errors="replace").rstrip())

        return int(b"".join(result))
