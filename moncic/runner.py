from __future__ import annotations
from typing import List, Dict, Any
import logging
import asyncio
import shlex

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
