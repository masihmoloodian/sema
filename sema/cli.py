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


@main.command()
@click.option("--uninstall", is_flag=True, help="Remove sema from Claude Code")
def init(uninstall: bool):
    """Register sema as an MCP server with Claude Code."""
    import subprocess
    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR

    if uninstall:
        ok = _claude_mcp_remove(scope="user")
        if ok:
            console.print("[yellow]✔[/yellow] Removed sema MCP server")
        else:
            console.print("[red]✗[/red] Could not remove via 'claude mcp remove'. Is the claude CLI installed?")

        # Kill any running sema serve processes for this project
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
def status():
    """Show index stats and MCP registration status."""
    project_root = Path(".").resolve()
    meta_path = project_root / DEFAULT_META_FILE

    if not meta_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    meta = json.loads(meta_path.read_text())

    table = Table(title="sema status", show_header=False)
    table.add_column("Key", style="dim")
    table.add_column("Value")
    table.add_row("Project", str(project_root))
    table.add_row("Index", DEFAULT_INDEX_DIR)
    table.add_row("Chunks", str(meta.get("chunk_count", "?")))
    table.add_row("Files", str(meta.get("file_count", "?")))
    table.add_row("Model", meta.get("model", "?"))
    table.add_row("Updated", meta.get("indexed_at", "?"))
    table.add_row("sema version", meta.get("sema_version", "?"))
    console.print(table)


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
