from __future__ import annotations
from typing import List, Dict, Any
import subprocess
import logging
import asyncio
import shlex
import time
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
        self.run_script = os.path.join(self.workdir, "script")
        self.retcode_file = os.path.join(self.workdir, "retcode")

    def run(self):
        # os.mkfifo(self.fifo_stdout)
        # os.mkfifo(self.fifo_stderr)

        try:
            script = [
                "#!/bin/sh",
                'cleanup() {'
                '   rm -f "$0"',
                '}',
                "trap cleanup EXIT",
                " ".join(shlex.quote(c) for c in self.cmd) + " > /root/transfer/stdout 2> /root/transfer/stderr",
                "echo $? > /root/transfer/retcode",
            ]

            with open(self.run_script, "wt") as fd:
                for line in script:
                    print(line, file=fd)

            cmd = [
                "systemd-run", "--quiet", "--setenv=HOME=/root",
                f"--machine={self.machine_name}", "--", "/bin/sh", "/root/transfer/script"
            ]

            log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))
            res = subprocess.run(cmd, check=True)

            result = self.poll_command_results()
            retcode = result["returncode"]

            # TODO: collect stdout/stderr into RunFailed?
            # TODO: but still let it run on stdout/stderr for progress?
            if retcode != 0:
                raise RunFailed(f"Run script exited with code {res.returncode}")

            return result
        finally:
            pass
            # os.unlink(self.fifo_stdout)
            # os.unlink(self.fifo_stderr)

    def poll_command_results(self) -> Dict[str, Any]:
        while True:
            if os.path.exists(self.run_script):
                time.sleep(0.2)
                continue

            return {
                "returncode": int(open(self.retcode_file).read()),
                "stdout": open(self.fifo_stdout, "rb").read(),
                "stderr": open(self.fifo_stderr, "rb").read(),
            }
