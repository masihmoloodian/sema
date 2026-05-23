"""sema CLI — index, init, serve, search, status."""

import shutil
import json
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_INDEX_DIR = ".sema/index"
DEFAULT_META_FILE = ".sema/meta.json"


@click.group()
@click.version_option()
def main():
    """sema — semantic codebase indexer for Claude Code."""
    pass


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--workspace", type=click.Path(exists=True), help="VS Code .code-workspace file — index only its listed folders")
@click.option("--reset", is_flag=True, help="Delete existing index and re-index")
@click.option("--verbose", is_flag=True, help="Show each file indexed")
def index(path: str, workspace: str | None, reset: bool, verbose: bool):
    """Index a codebase directory (or a VS Code workspace)."""
    from .indexer.chunker import index_project
    from .indexer.embedder import Embedder
    from .store.chroma import SemaStore

    import datetime
    import importlib.metadata

    # Resolve workspace root and folders to index
    if workspace:
        workspace_file = Path(workspace).resolve()
        workspace_root = workspace_file.parent
        ws_data = json.loads(workspace_file.read_text())
        folders = [
            workspace_root / f["path"]
            for f in ws_data.get("folders", [])
        ]
        missing = [f for f in folders if not f.exists()]
        if missing:
            for m in missing:
                console.print(f"[yellow]⚠[/yellow]  Skipping missing folder: {m}")
            folders = [f for f in folders if f.exists()]
        if not folders:
            console.print("[red]✗[/red] No valid folders found in workspace file.")
            return
        index_root = workspace_root
        console.print(f"[bold]Workspace[/bold] {workspace_file.name}  ({len(folders)} folders)")
    else:
        index_root = Path(path).resolve()
        folders = [index_root]
        console.print(f"[bold]Indexing[/bold] {index_root}")

    index_path = index_root / DEFAULT_INDEX_DIR
    store = SemaStore(index_path)
    embedder = Embedder()

    total = {"files": 0, "chunks": 0, "languages": {}, "skipped": 0}
    base_root = index_root if workspace else None
    for folder in folders:
        if workspace:
            console.print(f"  [dim]→[/dim] {folder.name}")
        stats = index_project(folder, store, embedder, reset=reset, base_root=base_root)
        total["files"] += stats["files"]
        total["chunks"] += stats["chunks"]
        total["skipped"] += stats.get("skipped", 0)
        for lang, count in stats["languages"].items():
            total["languages"][lang] = total["languages"].get(lang, 0) + count
        reset = False  # only wipe on first folder to avoid clearing previous results

    skipped = total["skipped"]
    skip_note = f" [dim]({skipped} unchanged, skipped)[/dim]" if skipped else ""
    console.print(f"\n[green]✔[/green] Indexed [bold]{total['files']}[/bold] files{skip_note}")
    console.print(f"[green]✔[/green] Generated [bold]{total['chunks']}[/bold] chunks")
    for lang, count in total["languages"].items():
        console.print(f"    {lang}: {count}")

    try:
        sema_version = importlib.metadata.version("sema")
    except importlib.metadata.PackageNotFoundError:
        sema_version = "dev"

    meta = {
        "version": "1",
        "model": "all-MiniLM-L6-v2",
        "indexed_at": datetime.datetime.utcnow().isoformat() + "Z",
        "chunk_count": total["chunks"],
        "file_count": total["files"],
        "sema_version": sema_version,
    }
    meta_path = index_root / DEFAULT_META_FILE
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))

    console.print(f"\n[green]✔[/green] Stored in {DEFAULT_INDEX_DIR}/")
    console.print("\nRun [bold]sema init[/bold] to register with Claude Code.")


def _find_claude_bin() -> str | None:
    """Find the claude CLI binary, checking PATH and known install locations."""
    if found := shutil.which("claude"):
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _claude_mcp_add(project_root: Path, scope: str = "user") -> bool:
    """Register sema via `claude mcp add`. Returns True on success."""
    import subprocess, sys
    sema_bin = shutil.which("sema") or str(Path(sys.executable).parent / "sema")
    claude_bin = _find_claude_bin()
    if not claude_bin:
        return False
    result = subprocess.run(
        [claude_bin, "mcp", "add", "sema", "-s", scope,
         "--", sema_bin, "serve", "--project", str(project_root)],
        capture_output=True, text=True,
    )
    # Exit 1 with "already exists" is fine — server is registered
    if result.returncode != 0 and "already exists" not in result.stderr + result.stdout:
        return False
    return True


def _claude_mcp_remove(scope: str = "user") -> bool:
    """Remove sema via `claude mcp remove`. Returns True on success."""
    import subprocess
    claude_bin = _find_claude_bin()
    if not claude_bin:
        return False
    result = subprocess.run(
        [claude_bin, "mcp", "remove", "sema", "-s", scope],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _codex_config_add(project_root: Path) -> tuple[bool, Path]:
    """Write [mcp_servers.sema] into <project>/.codex/config.toml. Returns (changed, config_path).

    Uses project-level config (not ~/.codex/config.toml) so the hardcoded project
    path is correct — Codex does not support {workspace_folder} template substitution.
    """
    import sys
    sema_bin = shutil.which("sema") or str(Path(sys.executable).parent / "sema")
    config_path = project_root / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    block = (
        "\n[mcp_servers.sema]\n"
        "enabled = true\n"
        f'command = "{sema_bin}"\n'
        f'args = ["serve", "--project", "{project_root}"]\n'
        "startup_timeout_sec = 15.0\n"
        "tool_timeout_sec = 60.0\n"
    )

    existing = config_path.read_text() if config_path.exists() else ""
    if "[mcp_servers.sema]" in existing:
        return False, config_path  # already present

    config_path.write_text(existing.rstrip() + block)
    return True, config_path


def _codex_config_remove(config_path: Path) -> bool:
    """Remove the [mcp_servers.sema] block from config.toml. Returns True if removed."""
    if not config_path.exists():
        return False
    lines = config_path.read_text().splitlines(keepends=True)
    out, inside = [], False
    for line in lines:
        if line.strip() == "[mcp_servers.sema]":
            inside = True
            continue
        if inside and line.startswith("["):
            inside = False
        if not inside:
            out.append(line)
    if len(out) == len(lines):
        return False
    config_path.write_text("".join(out))
    return True


@main.command()
@click.option("--uninstall", is_flag=True, help="Remove sema from Claude Code / Codex")
@click.option("--codex", "target", flag_value="codex", help="Register with OpenAI Codex")
@click.option("--claude", "target", flag_value="claude", default=True, help="Register with Claude Code (default)")
def init(uninstall: bool, target: str):
    """Register sema as an MCP server with Claude Code or OpenAI Codex."""
    import subprocess
    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR

    if target == "codex":
        _init_codex(uninstall, project_root, index_path)
    else:
        _init_claude(uninstall, project_root, index_path)


def _init_claude(uninstall: bool, project_root: Path, index_path: Path) -> None:
    import subprocess
    if uninstall:
        ok = _claude_mcp_remove(scope="user")
        if ok:
            console.print("[yellow]✔[/yellow] Removed sema MCP server")
        else:
            console.print("[red]✗[/red] Could not remove via 'claude mcp remove'. Is the claude CLI installed?")

        try:
            result = subprocess.run(
                ["pgrep", "-f", f"sema serve --project {project_root}"],
                capture_output=True, text=True,
            )
            pids = result.stdout.split()
            if pids:
                subprocess.run(["kill"] + pids, check=False)
                console.print(f"[yellow]✔[/yellow] Stopped {len(pids)} sema serve process(es)")
        except FileNotFoundError:
            pass
        return

    if not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    ok = _claude_mcp_add(project_root, scope="user")
    if ok:
        console.print("[green]✔[/green] Registered as MCP server 'sema' (user scope)")
        console.print("\n[bold]Done.[/bold] Run [bold]/mcp[/bold] in Claude Code to confirm.")
    else:
        console.print("[red]✗[/red] Could not register via 'claude mcp add'. Is the claude CLI installed?")
        console.print(f"\nRun manually:\n  claude mcp add sema -s user -- sema serve --project {project_root}")


def _init_codex(uninstall: bool, project_root: Path, index_path: Path) -> None:
    config_path = project_root / ".codex" / "config.toml"
    if uninstall:
        removed = _codex_config_remove(config_path)
        if removed:
            console.print(f"[yellow]✔[/yellow] Removed \\[mcp_servers.sema] from {config_path}")
        else:
            console.print(f"[yellow]–[/yellow] \\[mcp_servers.sema] not found in {config_path}")
        return

    if not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    changed, config_path = _codex_config_add(project_root)
    if changed:
        console.print(f"[green]✔[/green] Registered as MCP server 'sema' (project scope)")
        console.print(f"[dim]  {config_path}[/dim]")
    else:
        console.print(f"[yellow]–[/yellow] Already registered in {config_path}")
    console.print("\n[bold]Done.[/bold] Run [bold]/mcp[/bold] in Codex to confirm.")


@main.command()
@click.argument("query")
@click.option("--top-k", default=5, help="Number of results")
@click.option("--all-types", is_flag=True, help="Include docs/config sections (default: code only)")
def search(query: str, top_k: int, all_types: bool):
    """Search the codebase index. Useful for testing without Claude."""
    from .store.chroma import SemaStore
    from .store.bm25 import BM25Index
    from .indexer.embedder import Embedder
    from .mcp.tools import _CODE_CHUNK_TYPES, _rrf_merge

    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR

    if not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    store = SemaStore(index_path)
    embedder = Embedder()

    chunk_types = None if all_types else _CODE_CHUNK_TYPES
    fetch_k = min(top_k * 3, 30)

    embedding = embedder.embed_one(query)
    semantic = store.search(embedding, top_k=fetch_k, chunk_types=chunk_types)

    # Build BM25 and merge
    ids, metadatas = store.get_all_for_bm25()
    if ids:
        texts = [f"{m['name']} {m['signature']}" for m in metadatas]
        bm25 = BM25Index(ids, texts, metadatas)
        bm25_results = bm25.search(query, top_k=fetch_k, chunk_types=chunk_types)
        if bm25_results and bm25_results[0]["score"] >= 5.0:
            results = _rrf_merge(semantic, bm25_results, top_k=top_k)
        else:
            results = semantic[:top_k]
    else:
        results = semantic[:top_k]

    if not results:
        console.print("No results found.")
        return

    console.print(f"\n[bold]Results for '{query}':[/bold]\n")
    for r in results:
        score_pct = int(r["score"] * 100)
        console.print(
            f"  [cyan]{r['file']}::{r['name']}[/cyan]  "
            f"[dim]line {r['start_line']}[/dim]  "
            f"[green]{score_pct}% match[/green]"
        )
        console.print(f"    [dim]{r['type']}:[/dim] {r['signature']}\n")


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show full details including MCP registration and binary paths.")
def status(verbose: bool):
    """Show index stats and MCP registration status."""
    import subprocess
    import shutil as _shutil

    project_root = Path(".").resolve()
    meta_path = project_root / DEFAULT_META_FILE

    # ── Index ─────────────────────────────────────────────────────────────────
    console.print()
    index_path = project_root / DEFAULT_INDEX_DIR
    if not meta_path.exists():
        console.print(f"[bold]Index[/bold]  [red]✗ No index found[/red] — run [bold]sema index .[/bold]")
    else:
        meta = json.loads(meta_path.read_text())

        # Read live counts from ChromaDB — meta.json only stores last-run delta
        total_chunks = "?"
        total_files = "?"
        try:
            from .store.chroma import SemaStore
            store = SemaStore(index_path)
            all_meta = store.get_all_metadata()
            total_chunks = len(all_meta)
            total_files = len({m.get("file", "") for m in all_meta if m.get("file")})
        except Exception:
            total_chunks = meta.get("chunk_count", meta.get("chunks", "?"))
            total_files = meta.get("file_count", meta.get("files", "?"))

        console.print(f"[bold]Index[/bold]")
        console.print(f"  Project  {project_root}")
        console.print(f"  Chunks   {total_chunks}")
        console.print(f"  Files    {total_files}")
        console.print(f"  Updated  {meta.get('indexed_at', '?')}")
        console.print(f"  Model    {meta.get('model', '?')}")
        if verbose:
            console.print(f"  Path     {project_root / DEFAULT_INDEX_DIR}")
            console.print(f"  Version  {meta.get('sema_version', '?')}")
            langs = meta.get("languages", {})
            if langs:
                console.print(f"  Languages")
                for lang, count in sorted(langs.items()):
                    console.print(f"    [dim]{lang}: {count}[/dim]")

    # ── MCP server — what project is it serving? ──────────────────────────────
    import re as _re

    def _print_serving(serving: str | None, project_root: Path, fix_cmd: str) -> None:
        if not serving:
            return
        match = Path(serving).resolve() == project_root
        color = "green" if match else "yellow"
        console.print(f"  Serving      [{color}]{serving}[/{color}]")
        if not match:
            console.print(f"  [yellow]  ⚠  Serving a different project than cwd[/yellow]")
            console.print(f"  [dim]     cwd:     {project_root}[/dim]")
            console.print(f"  [dim]     serving: {serving}[/dim]")
            console.print(f"  [dim]     Fix: {fix_cmd}[/dim]")

    console.print()
    console.print(f"[bold]MCP server[/bold]")

    # Claude Code
    claude = _shutil.which("claude")
    if claude:
        result = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True)
        output = result.stdout + result.stderr
        if "sema" in output:
            for line in output.splitlines():
                if "sema" in line and ":" in line:
                    serving = None
                    if "--project" in line:
                        parts = line.split("--project")
                        if len(parts) > 1:
                            serving = parts[1].strip().split()[0]

                    if "Failed" in line:
                        console.print(f"  Claude Code  [red]✗ Failed[/red]")
                        console.print(f"  [dim]  Fix: sema init --claude --uninstall && sema init --claude[/dim]")
                    elif "Connected" in line or "✓" in line:
                        console.print(f"  Claude Code  [green]✔ Connected[/green]")
                    else:
                        console.print(f"  Claude Code  [yellow]⚠ Registered (not connected)[/yellow]")

                    _print_serving(serving, project_root, "sema init --claude --uninstall && sema init --claude")

                    if verbose:
                        claude_cfg = Path.home() / ".claude.json"
                        console.print(f"  [dim]  config:  {claude_cfg}[/dim]")
                        console.print(f"  [dim]  command: {serving and f'sema serve --project {serving}'}[/dim]")
        else:
            console.print(f"  Claude Code  [yellow]–[/yellow] not registered — run: sema init --claude")
    else:
        console.print(f"  Claude Code  [dim]–[/dim] claude CLI not found")

    # Codex
    codex_config = project_root / ".codex" / "config.toml"
    if codex_config.exists():
        content = codex_config.read_text()
        if "[mcp_servers.sema]" in content:
            serving = None
            m = _re.search(r'"--project",\s*"([^"]+)"', content)
            if m:
                serving = m.group(1)

            # Check if binary in config exists
            cmd_ok = True
            cm = _re.search(r'^command\s*=\s*"([^"]+)"', content, _re.MULTILINE)
            if cm and not Path(cm.group(1)).exists():
                console.print(f"  Codex        [red]✗ Failed[/red]")
                console.print(f"  [dim]  Binary not found: {cm.group(1)}[/dim]")
                console.print(f"  [dim]  Fix: sema init --codex --uninstall && sema init --codex[/dim]")
                cmd_ok = False

            if cmd_ok:
                console.print(f"  Codex        [green]✔ Connected[/green]")

            _print_serving(serving, project_root, "sema init --codex --uninstall && sema init --codex")

            if verbose:
                console.print(f"  [dim]  config:  {codex_config}[/dim]")
        else:
            console.print(f"  Codex        [yellow]–[/yellow] not registered — run: sema init --codex")
    else:
        console.print(f"  Codex        [dim]–[/dim] not registered — run: sema init --codex")

    # Binary
    if verbose:
        console.print()
        console.print(f"[bold]Binary[/bold]")
        binary = _shutil.which("sema")
        console.print(f"  Path     {binary or '[red]not found[/red]'}")
        import sys
        console.print(f"  Python   {sys.executable}")
    console.print()


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--workspace", type=click.Path(exists=True), help="VS Code .code-workspace file")
def watch(path: str, workspace: str | None):
    """Watch for file changes and re-index automatically.

    Re-indexes only the changed file on each save — not the whole project.
    Run this in a terminal alongside your editor. Requires an existing index
    (run sema index . first).
    """
    import datetime
    from .indexer.embedder import Embedder
    from .store.chroma import SemaStore
    from .store.hashes import FileHashStore
    from .utils.watcher import start_watch

    if workspace:
        workspace_file = Path(workspace).resolve()
        watch_root = workspace_file.parent
        ws_data = json.loads(workspace_file.read_text())
        watch_dirs = [
            watch_root / f["path"]
            for f in ws_data.get("folders", [])
            if (watch_root / f["path"]).exists()
        ]
        base_root = watch_root
        console.print(f"[bold]Watching workspace[/bold] {workspace_file.name}  ({len(watch_dirs)} folders)")
    else:
        watch_root = Path(path).resolve()
        watch_dirs = [watch_root]
        base_root = watch_root
        console.print(f"[bold]Watching[/bold] {watch_root}")

    index_path = watch_root / DEFAULT_INDEX_DIR
    if not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index[/bold] first.")
        return

    store = SemaStore(index_path)
    embedder = Embedder()
    hash_store = FileHashStore(index_path.parent)

    console.print("[dim]Re-indexing changed files automatically. Press Ctrl+C to stop.[/dim]\n")

    def on_indexed(file_path: Path, n_chunks: int) -> None:
        try:
            rel = file_path.relative_to(base_root)
        except ValueError:
            rel = file_path
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if n_chunks == -1:
            console.print(f"[dim]{ts}[/dim]  [yellow]removed[/yellow]  {rel}")
        elif n_chunks == 0:
            console.print(f"[dim]{ts}[/dim]  [dim]skipped[/dim]   {rel}")
        else:
            console.print(f"[dim]{ts}[/dim]  [green]indexed[/green]   {rel}  [dim]({n_chunks} chunks)[/dim]")

    start_watch(watch_dirs, store, embedder, on_indexed=on_indexed, base_root=base_root, hash_store=hash_store)


@main.command()
@click.option("--project", default=".", type=click.Path(exists=True))
def serve(project: str):
    """Start MCP server (called automatically by Claude Code via mcp.json)."""
    from .mcp.server import serve as _serve
    project_root = Path(project).resolve()
    index_path = project_root / DEFAULT_INDEX_DIR
    _serve(project_root, index_path)


@main.command()
def doctor():
    """Diagnose sema installation and registration issues."""
    import sys
    import subprocess
    from datetime import datetime, timezone

    ok = True
    warnings = 0

    # ── 1. Binary ────────────────────────────────────────────────────────────
    binary = shutil.which("sema")
    console.print(f"\n[bold]1. Binary[/bold]")
    if binary:
        console.print(f"  [green]✔[/green] Found: {binary}")
    else:
        console.print(f"  [red]✗[/red] sema not found on PATH")
        console.print(f"  [dim]  Fix: add sema's .venv/bin to PATH, then source ~/.zshrc[/dim]")
        ok = False

    # ── 2. Venv mismatch ─────────────────────────────────────────────────────
    console.print(f"\n[bold]2. Python environment[/bold]")
    python = sys.executable
    console.print(f"  [dim]  Python: {python}[/dim]")
    if binary:
        binary_venv = Path(binary).parent.parent
        python_venv = Path(python).parent.parent
        if binary_venv == python_venv:
            console.print(f"  [green]✔[/green] Binary and Python are in the same venv")
        else:
            console.print(f"  [red]✗[/red] Venv mismatch")
            console.print(f"  [dim]  Binary:  {binary_venv}[/dim]")
            console.print(f"  [dim]  Python:  {python_venv}[/dim]")
            console.print(f"  [dim]  Fix: re-register after confirming `which sema` is correct[/dim]")
            ok = False

    # ── 3. Package importable ────────────────────────────────────────────────
    console.print(f"\n[bold]3. Package[/bold]")
    try:
        import sema  # noqa: F401
        console.print(f"  [green]✔[/green] sema package importable")
    except ImportError:
        console.print(f"  [red]✗[/red] sema package not installed in this venv")
        console.print(f"  [dim]  Fix: cd /path/to/sema && uv pip install -e '.[dev]'[/dim]")
        ok = False

    # ── 4. Claude Code registration ──────────────────────────────────────────
    console.print(f"\n[bold]4. Claude Code registration[/bold]")
    claude = shutil.which("claude")
    if claude:
        result = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True)
        output = result.stdout + result.stderr
        if "sema" in output:
            for line in output.splitlines():
                if "sema" in line and ":" in line:
                    if "Failed" in line:
                        console.print(f"  [red]✗[/red] {line.strip()}")
                        console.print(f"  [dim]  Registered binary may be wrong — run: sema init --claude --uninstall && sema init --claude[/dim]")
                        ok = False
                    elif "Connected" in line or "✓" in line:
                        console.print(f"  [green]✔[/green] {line.strip()}")
                    else:
                        console.print(f"  [dim]  {line.strip()}[/dim]")
            # Check if registered binary actually exists
            for line in output.splitlines():
                if "sema" in line and "/" in line:
                    parts = line.split()
                    for part in parts:
                        if part.startswith("/") and "sema" in part and not part.endswith("sema"):
                            continue
                        if part.startswith("/") and part.endswith("sema"):
                            if not Path(part).exists():
                                console.print(f"  [red]✗[/red] Registered binary does not exist: {part}")
                                console.print(f"  [dim]  Fix: sema init --claude --uninstall && sema init --claude[/dim]")
                                ok = False
        else:
            console.print(f"  [yellow]–[/yellow] sema not registered with Claude Code")
            console.print(f"  [dim]  Fix: sema init --claude[/dim]")
            warnings += 1

        # Check for stale project-level config (old sema versions wrote here)
        old_config = Path(".claude/settings.json")
        if old_config.exists():
            try:
                old_data = json.loads(old_config.read_text())
                if "mcpServers" in old_data and "sema" in old_data["mcpServers"]:
                    console.print(f"  [yellow]⚠[/yellow]  Old project-level config found: {old_config}")
                    console.print(f"  [dim]  This can conflict with user-level registration.[/dim]")
                    console.print(f"  [dim]  Fix: remove the 'sema' key from {old_config}[/dim]")
                    warnings += 1
            except Exception:
                pass
    else:
        console.print(f"  [dim]–[/dim] claude CLI not found — skipping")

    # ── 5. Codex registration ────────────────────────────────────────────────
    console.print(f"\n[bold]5. Codex registration[/bold]")
    codex_config = Path(".codex/config.toml")
    if codex_config.exists():
        content = codex_config.read_text()
        if "[mcp_servers.sema]" in content:
            console.print(f"  [green]✔[/green] Registered in {codex_config}")
            # Check if the binary path inside config exists
            for line in content.splitlines():
                if line.strip().startswith("command"):
                    cmd_path = line.split("=", 1)[1].strip().strip('"')
                    if not Path(cmd_path).exists():
                        console.print(f"  [red]✗[/red] Registered binary does not exist: {cmd_path}")
                        console.print(f"  [dim]  Fix: sema init --codex --uninstall && sema init --codex[/dim]")
                        ok = False
        else:
            console.print(f"  [yellow]–[/yellow] .codex/config.toml exists but sema not registered")
            console.print(f"  [dim]  Fix: sema init --codex[/dim]")
            warnings += 1
    else:
        console.print(f"  [dim]–[/dim] No .codex/config.toml — run sema init --codex if using Codex")

    # ── 6. Instruction file ──────────────────────────────────────────────────
    console.print(f"\n[bold]6. Instruction file (CLAUDE.md / AGENTS.md)[/bold]")
    claude_md = Path("CLAUDE.md")
    agents_md = Path("AGENTS.md")
    found_any = False
    for f in [claude_md, agents_md]:
        if f.exists():
            content = f.read_text()
            if "search_code" in content:
                console.print(f"  [green]✔[/green] {f} found and mentions search_code")
            else:
                console.print(f"  [yellow]⚠[/yellow]  {f} found but does not mention sema tools")
                console.print(f"  [dim]  Without search_code instructions the AI may not use sema[/dim]")
                warnings += 1
            found_any = True
    if not found_any:
        console.print(f"  [yellow]⚠[/yellow]  No CLAUDE.md or AGENTS.md in current directory")
        console.print(f"  [dim]  Without this file the AI may ignore sema and read files directly[/dim]")
        warnings += 1

    # ── 7. Lingering processes ───────────────────────────────────────────────
    console.print(f"\n[bold]7. Running processes[/bold]")
    result = subprocess.run(["pgrep", "-f", "sema serve"], capture_output=True, text=True)
    pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    if pids:
        console.print(f"  [green]✔[/green] sema serve running (pid {', '.join(pids)})")
    else:
        console.print(f"  [dim]–[/dim] No sema serve process running (started on demand by AI tool)")

    # ── 8. Index ─────────────────────────────────────────────────────────────
    console.print(f"\n[bold]8. Index[/bold]")
    index_path = Path(".") / DEFAULT_INDEX_DIR
    meta_path = Path(".") / DEFAULT_META_FILE
    if index_path.exists():
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            console.print(f"  [green]✔[/green] Index found — {meta.get('files', '?')} files, {meta.get('chunks', '?')} chunks")
            console.print(f"  [dim]  model: {meta.get('model', '?')}[/dim]")
            # Warn if index is old
            ts = meta.get("indexed_at")
            if ts:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
                    days = age.days
                    if days > 7:
                        console.print(f"  [yellow]⚠[/yellow]  Index is {days} days old — consider re-indexing: sema index .")
                        warnings += 1
                except Exception:
                    pass
        else:
            console.print(f"  [green]✔[/green] Index directory exists")
    else:
        console.print(f"  [yellow]–[/yellow] No index in current directory")
        console.print(f"  [dim]  Fix: sema index .[/dim]")
        warnings += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    if ok and warnings == 0:
        console.print("[green]✔ Everything looks good.[/green]")
    elif ok:
        console.print(f"[yellow]⚠  No errors, but {warnings} warning(s) — see above.[/yellow]")
    else:
        console.print("[red]✗ Issues found — see above.[/red]")
