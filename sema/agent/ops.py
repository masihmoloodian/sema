"""
Management operations behind the terminal app's slash commands.

Index-query operations run **in process** against ``sema.mcp.tools`` — the same
code path the MCP server uses, so results cannot drift between surfaces.

Management operations (index, watch, doctor, setup, update) shell out to the
``sema`` CLI, using ``--json`` wherever the command supports it. Those commands
own real side effects and a lot of console formatting; invoking them keeps one
implementation rather than forking their logic here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

INDEX_DIR = ".sema/index"
META_FILE = ".sema/meta.json"


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor holding a `.sema` index, else the starting directory."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / INDEX_DIR).exists():
            return candidate
    return current


def has_index(root: Path) -> bool:
    return (root / INDEX_DIR).exists()


def sema_bin() -> str:
    """The sema executable, preferring the one running this process."""
    found = shutil.which("sema")
    if found:
        return found
    return sys.executable


def _argv(args: list[str]) -> list[str]:
    binary = sema_bin()
    if binary == sys.executable:
        return [binary, "-m", "sema.cli", *args]
    return [binary, *args]


@dataclass
class CommandResult:
    ok: bool
    output: str
    data: dict | list | None = None


async def run_cli(args: list[str], cwd: Path | None = None,
                  timeout: int = 900) -> CommandResult:
    """Run a `sema` subcommand and capture its output."""
    try:
        process = await asyncio.create_subprocess_exec(
            *_argv(args),
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except OSError as exc:
        return CommandResult(False, f"Could not run sema: {exc}")
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        return CommandResult(False, f"`sema {' '.join(args)}` timed out after {timeout}s")
    text = stdout.decode("utf-8", errors="replace").strip()
    data = None
    if "--json" in args:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    return CommandResult(process.returncode == 0, text, data)


# ── in-process index binding ────────────────────────────────────────────────


def silence_progress_bars() -> None:
    """Stop tqdm/HuggingFace from drawing into a TUI it does not own.

    sentence-transformers prints a download/encode progress bar on stderr; in a
    full-screen terminal app that lands on top of the layout.
    """
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # sentence-transformers logs model loading at INFO, and derives its
    # progress-bar default from the logger level — raising the level silences
    # both the log lines and the bar.
    for name in ("sentence_transformers", "transformers", "chromadb", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        import threading

        import tqdm as _tqdm

        # tqdm lazily builds a *multiprocessing* lock on first use. Inside a
        # full-screen app the file descriptors it needs to spawn the resource
        # tracker are gone, and that raises. A thread lock is all we need,
        # since the embedder only ever runs in a worker thread here.
        _tqdm.tqdm.set_lock(threading.RLock())
    except Exception:  # noqa: BLE001 - tqdm is a transitive dep; never fatal
        pass


def bind_index(root: Path) -> str | None:
    """Point ``sema.mcp.tools`` at this project's index.

    Returns None on success, or a user-facing message explaining why the index
    is unavailable — the agent still runs, just without the sema tools.
    """
    index_path = root / INDEX_DIR
    if not index_path.exists():
        return f"No index at {index_path}. Run `/index` to build one."
    try:
        from ..indexer.embedder import Embedder
        from ..mcp.tools import init_tools
        from ..store.chroma import SemaStore

        store = SemaStore(index_path)
        init_tools(store, Embedder(), root)
    except Exception as exc:  # noqa: BLE001 - degraded mode, not fatal
        return f"Could not open the index: {type(exc).__name__}: {exc}"
    return None


# ── index-query operations (in process) ─────────────────────────────────────


def search(query: str, top_k: int = 5, project: str | None = None) -> str:
    from ..mcp import tools

    return tools.search_code(query=query, top_k=top_k, project=project)


def get_code(symbol: str, project: str | None = None) -> str:
    from ..mcp import tools

    return tools.get_code(symbol_name=symbol, project=project)


def reuse(description: str, project: str | None = None) -> str:
    from ..mcp import tools

    return tools.check_reuse(description=description, project=project)


def repo_map(project: str | None = None) -> str:
    from ..mcp import tools

    return tools.repo_map(project=project)


def find_usages(symbol: str, project: str | None = None) -> str:
    from ..mcp import tools

    return tools.find_usages(symbol_name=symbol, project=project)


def impact(symbol: str, project: str | None = None) -> str:
    from ..mcp import tools

    return tools.impact_analysis(symbol_name=symbol, project=project)


def explain(file_path: str, project: str | None = None) -> str:
    from ..mcp import tools

    return tools.explain_file(file_path=file_path, project=project)


def list_projects() -> str:
    from ..mcp import tools

    return tools.list_projects()


# ── management operations (subprocess) ──────────────────────────────────────


async def index(root: Path, reset: bool = False, verbose: bool = False) -> CommandResult:
    args = ["index", str(root)]
    if reset:
        args.append("--reset")
    if verbose:
        args.append("--verbose")
    result = await run_cli(args, cwd=root)
    if result.ok:
        bind_index(root)
    return result


async def status(root: Path) -> CommandResult:
    return await run_cli(["status", "--json"], cwd=root, timeout=120)


async def doctor(root: Path) -> CommandResult:
    return await run_cli(["doctor"], cwd=root, timeout=300)


async def add_file(root: Path, path: str) -> CommandResult:
    return await run_cli(["add", path, "--root", str(root), "--json"], cwd=root)


async def remove_file(root: Path, path: str) -> CommandResult:
    return await run_cli(["remove", path, "--root", str(root), "--json"], cwd=root)


async def list_files(root: Path) -> CommandResult:
    return await run_cli(["list", str(root), "--json"], cwd=root)


async def setup(root: Path, uninstall: bool = False) -> CommandResult:
    args = ["setup"] + (["--uninstall"] if uninstall else [])
    return await run_cli(args, cwd=root, timeout=600)


async def init_client(root: Path, target: str, uninstall: bool = False) -> CommandResult:
    args = ["init", f"--{target}"] + (["--uninstall"] if uninstall else [])
    return await run_cli(args, cwd=root, timeout=600)


async def update_agents(check: bool = False) -> CommandResult:
    args = ["update"] + (["--check"] if check else [])
    return await run_cli(args, timeout=900)


async def self_update() -> CommandResult:
    return await run_cli(["self-update"], timeout=900)


# ── watch (long-lived child process) ────────────────────────────────────────


class Watcher:
    """Supervises a `sema watch` child so the TUI can toggle auto-indexing."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._process: asyncio.subprocess.Process | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> str:
        if self.running:
            return "Watch is already running."
        try:
            self._process = await asyncio.create_subprocess_exec(
                *_argv(["watch", str(self.root)]),
                cwd=str(self.root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            return f"Could not start watch: {exc}"
        return "Watch started — the index now follows file changes."

    async def stop(self) -> str:
        if not self.running:
            return "Watch is not running."
        assert self._process is not None
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self._process.kill()
        self._process = None
        return "Watch stopped."

    async def toggle(self) -> str:
        return await (self.stop() if self.running else self.start())


# ── devops gate ─────────────────────────────────────────────────────────────


def devops_pending(root: Path) -> list[dict]:
    from ..devops import gate

    return gate.pending_actions(root)


def devops_approve(root: Path, action_id: str) -> dict:
    from ..devops import gate

    return gate.approve(action_id, root)


def devops_deny(root: Path, action_id: str, reason: str | None = None) -> dict:
    from ..devops import gate

    return gate.deny(action_id, root, reason)


def devops_log(root: Path, limit: int = 50) -> list[dict]:
    from ..devops import gate

    return gate.audit_log(root, limit)


def devops_plan(argv: list[str]) -> dict:
    from ..devops import gate

    return gate.plan(argv)


# ── redaction ───────────────────────────────────────────────────────────────


def redact_text(text: str) -> tuple[str, list]:
    """Scrub PII before text leaves the machine. Returns (clean, entities)."""
    try:
        from ..redact import redact_text as _redact
    except ImportError:
        return text, []
    try:
        result = _redact(text)
    except Exception:  # noqa: BLE001 - spaCy model may be absent; never block a turn
        return text, []
    return result.get("text", text), result.get("entities", [])
