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
@click.option("--reset", is_flag=True, help="Delete existing index and re-index")
@click.option("--verbose", is_flag=True, help="Show each file indexed")
def index(path: str, reset: bool, verbose: bool):
    """Index a codebase directory."""
    from .indexer.chunker import index_project
    from .indexer.embedder import Embedder
    from .store.chroma import SemaStore

    project_root = Path(path).resolve()
    index_path = project_root / DEFAULT_INDEX_DIR

    console.print(f"[bold]Indexing[/bold] {project_root}")

    store = SemaStore(index_path)
    embedder = Embedder()

    stats = index_project(project_root, store, embedder, reset=reset)

    console.print(f"\n[green]✔[/green] Indexed [bold]{stats['files']}[/bold] files")
    console.print(f"[green]✔[/green] Generated [bold]{stats['chunks']}[/bold] chunks")
    for lang, count in stats["languages"].items():
        console.print(f"    {lang}: {count}")

    import datetime
    import importlib.metadata
    try:
        sema_version = importlib.metadata.version("sema")
    except importlib.metadata.PackageNotFoundError:
        sema_version = "dev"

    meta = {
        "version": "1",
        "model": "all-MiniLM-L6-v2",
        "indexed_at": datetime.datetime.utcnow().isoformat() + "Z",
        "chunk_count": stats["chunks"],
        "file_count": stats["files"],
        "sema_version": sema_version,
    }
    meta_path = project_root / DEFAULT_META_FILE
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))

    console.print(f"\n[green]✔[/green] Stored in {DEFAULT_INDEX_DIR}/")
    console.print("\nRun [bold]sema init[/bold] to register with Claude Code.")


def _write_mcp_config(project_root: Path, index_path: Path) -> None:
    """Write .claude/settings.json to register sema as an MCP server."""
    import sys
    sema_bin = shutil.which("sema") or str(Path(sys.executable).parent / "sema")
    settings_dir = project_root / ".claude"
    settings_dir.mkdir(exist_ok=True)
    settings_file = settings_dir / "settings.json"
    existing = json.loads(settings_file.read_text()) if settings_file.exists() else {}
    existing.setdefault("mcpServers", {})["sema"] = {
        "command": sema_bin,
        "args": ["serve", "--project", str(project_root)],
    }
    settings_file.write_text(json.dumps(existing, indent=2))
    console.print(f"[green]✔[/green] Wrote MCP config to {settings_file}")


@main.command()
@click.option("--uninstall", is_flag=True, help="Remove sema from Claude Code config")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def init(uninstall: bool, dry_run: bool):
    """Register sema as an MCP server with Claude Code."""
    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR

    if uninstall:
        settings_file = project_root / ".claude" / "settings.json"
        if not settings_file.exists():
            console.print("[dim]No .claude/settings.json found — nothing to remove.[/dim]")
            return
        existing = json.loads(settings_file.read_text())
        if "sema" not in existing.get("mcpServers", {}):
            console.print("[dim]sema is not registered in .claude/settings.json[/dim]")
            return
        if dry_run:
            console.print(f"[dim]Would remove mcpServers.sema from {settings_file}[/dim]")
            return
        del existing["mcpServers"]["sema"]
        if not existing["mcpServers"]:
            del existing["mcpServers"]
        settings_file.write_text(json.dumps(existing, indent=2))
        console.print(f"[yellow]✔[/yellow] Removed sema from {settings_file}")
        console.print("Reload VS Code to apply the change.")
        return

    if not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    console.print("Registering with Claude Code...")

    if not dry_run:
        _write_mcp_config(project_root, index_path)

    console.print("[green]✔[/green] Registered as MCP server 'sema'")

    gitignore = Path(".gitignore")
    entry = "\n# sema\n.sema/index/\n"
    if not dry_run and gitignore.exists():
        content = gitignore.read_text()
        if ".sema/index/" not in content:
            if click.confirm("Add .sema/index/ to .gitignore?", default=True):
                gitignore.write_text(content + entry)
                console.print("[green]✔[/green] Updated .gitignore")

    console.print("\n[bold]Done.[/bold] Restart VS Code or run /mcp in Claude chat to confirm.")


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
@click.option("--project", default=".", type=click.Path(exists=True))
def serve(project: str):
    """Start MCP server (called automatically by Claude Code via mcp.json)."""
    from .mcp.server import serve as _serve
    project_root = Path(project).resolve()
    index_path = project_root / DEFAULT_INDEX_DIR
    _serve(project_root, index_path)
