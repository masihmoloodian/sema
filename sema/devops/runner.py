"""Executes a devops command and captures its output.

The only place in this feature that actually runs a subprocess. Callers must
have already cleared the command through policy.classify() — this module
does not re-check anything, by design: it's the exec step, not the gate.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

_TIMEOUT_SECONDS = 120

# Some tools (Terraform in particular) still emit ANSI color codes even when
# stdout isn't a tty. Left in, they've been observed confusing the NER
# redaction pass into misfiring on fragments of the escape sequences,
# corrupting otherwise-clean output with spurious [NAME]/[LOCATION] tags —
# so output is normalized here, before it reaches redaction, logging, or
# display.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def execute(argv: list[str], timeout: int = _TIMEOUT_SECONDS) -> RunResult:
    # Belt-and-suspenders: ask well-behaved tools not to color in the first
    # place (NO_COLOR is a broad convention; CLICOLOR=0 covers a few more).
    # ANSI is still stripped below regardless, since not every tool honors these.
    env = {**os.environ, "NO_COLOR": "1", "CLICOLOR": "0"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        return RunResult(exit_code=proc.returncode, stdout=_strip_ansi(proc.stdout), stderr=_strip_ansi(proc.stderr))
    except subprocess.TimeoutExpired as e:
        return RunResult(exit_code=124, stdout=_strip_ansi(e.stdout or ""), stderr=f"timed out after {timeout}s")
    except FileNotFoundError as e:
        return RunResult(exit_code=127, stdout="", stderr=str(e))
