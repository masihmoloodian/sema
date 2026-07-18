"""sema CLI — index, init, serve, search, status."""

import shutil
import json
import subprocess
from pathlib import Path
import click
from rich.console import Console

console = Console()

DEFAULT_INDEX_DIR = ".sema/index"
DEFAULT_META_FILE = ".sema/meta.json"


def _launcher_environment(executable: str | Path) -> Path:
    """Return a command launcher's environment root, following uv/pipx bin symlinks."""
    return Path(executable).resolve().parent.parent


def _emit_json(data: object) -> None:
    """Print a value as plain JSON on stdout (no ANSI) for editors/scripts."""
    click.echo(json.dumps(data, indent=2, default=str))


@click.group()
@click.version_option()
def main():
    """sema — semantic codebase indexer for Claude Code."""
    pass


# Updates re-run each agent's official install script (the curl one-liner from its docs)
# instead of the CLI's own self-updater — the self-updaters fail for some install methods,
# which is the error users hit when updating from the extension. Re-running the installer
# is idempotent and preserves auth/config.
_AGENT_CLIS = {
    "claude": {"binary": "claude", "install": "curl -fsSL https://claude.ai/install.sh | bash", "version": ["--version"], "label": "Claude Code"},
    "codex": {"binary": "codex", "install": "curl -fsSL https://chatgpt.com/codex/install.sh | sh", "version": ["--version"], "label": "Codex"},
    "opencode": {"binary": "opencode", "install": "curl -fsSL https://raw.githubusercontent.com/opencode-ai/opencode/refs/heads/main/install | bash", "version": ["--version"], "label": "opencode"},
    "grok": {"binary": "grok", "install": "curl -fsSL https://x.ai/cli/install.sh | bash", "version": ["--version"], "label": "Grok Build"},
}


@main.command(name="update")
@click.option(
    "--provider",
    "providers",
    multiple=True,
    type=click.Choice(tuple(_AGENT_CLIS)),
    help="Update only this agent CLI (repeatable). Defaults to every installed agent.",
)
@click.option("--check", is_flag=True, help="Show installed versions without updating.")
def update_agents(providers: tuple[str, ...], check: bool) -> None:
    """Check or update supported coding-agent CLIs.

    Updates re-run each agent's official install script (the curl one-liner from its
    docs) rather than the CLI's own self-updater, which errors for some install methods.
    Authentication and configuration are preserved. `--check` just prints versions.
    """
    selected = providers or tuple(_AGENT_CLIS)
    failures = 0
    found = 0
    for provider in selected:
        spec = _AGENT_CLIS[provider]
        binary = shutil.which(spec["binary"])
        label = spec["label"]
        if not binary:
            console.print(f"[dim]–[/dim] {label}: not installed")
            continue
        found += 1
        if check:
            result = subprocess.run([binary, *spec["version"]], check=False)
        else:
            console.print(f"\n[bold]Updating {label}[/bold]  [dim]{spec['install']}[/dim]")
            # The official installer pipes curl into a shell, so run it through one.
            result = subprocess.run(spec["install"], shell=True, check=False)
        if result.returncode != 0:
            failures += 1
            console.print(f"[red]✗[/red] {label}: command exited {result.returncode}")
    if not found:
        raise click.ClickException("No supported agent CLIs were found on PATH.")
    if failures:
        raise click.ClickException(f"{failures} agent update(s) failed.")
    if not check:
        console.print("\n[green]✔[/green] Agent CLI updates finished. Restart active agent sessions and reload the extension to refresh models.")


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
    from .utils.gitignore import ensure_entry

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
        sema_version = importlib.metadata.version("sema-mcp")
    except importlib.metadata.PackageNotFoundError:
        sema_version = "dev"

    # `total` describes work performed by this invocation, not the whole index.
    # Metadata is consumed by doctor/status and must describe the committed store
    # after an incremental run where most files were skipped.
    all_meta = store.get_all_metadata()

    meta = {
        "version": "1",
        "model": "all-MiniLM-L6-v2",
        "indexed_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
        "chunk_count": len(all_meta),
        "file_count": len({m.get("file", "") for m in all_meta if m.get("file")}),
        "sema_version": sema_version,
    }
    meta_path = index_root / DEFAULT_META_FILE
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))

    console.print(f"\n[green]✔[/green] Stored in {DEFAULT_INDEX_DIR}/")

    # Keep the local index out of version control automatically.
    ignored = ensure_entry(index_root, ".sema/")
    if ignored == "created":
        console.print("[green]✔[/green] Created [bold].gitignore[/bold] with [bold].sema/[/bold]")
    elif ignored == "appended":
        console.print("[green]✔[/green] Added [bold].sema/[/bold] to [bold].gitignore[/bold]")

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


def _serve_args(project_root: Path | None, roots: list[Path] | None) -> list[str]:
    """Build the `serve ...` argv for a registration command."""
    if roots:
        args = ["serve"]
        for r in roots:
            args += ["--root", str(r)]
        return args
    return ["serve", "--project", str(project_root)]


def _claude_mcp_add(project_root: Path | None = None, roots: list[Path] | None = None, scope: str = "user") -> bool:
    """Register sema via `claude mcp add`. Returns True on success."""
    import subprocess
    import sys
    sema_bin = shutil.which("sema") or str(Path(sys.executable).parent / "sema")
    claude_bin = _find_claude_bin()
    if not claude_bin:
        return False
    result = subprocess.run(
        [claude_bin, "mcp", "add", "sema", "-s", scope,
         "--", sema_bin, *_serve_args(project_root, roots)],
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


def _sema_bin() -> str:
    """Absolute path to the sema binary, for baking into a client's config."""
    import sys
    return shutil.which("sema") or str(Path(sys.executable).parent / "sema")


def _toml_mcp_config_add(
    config_path: Path,
    project_root: Path,
    roots: list[Path] | None,
    *,
    startup_timeout: str,
    tool_timeout: str,
) -> tuple[bool, Path]:
    """Append [mcp_servers.sema] to a TOML config unless it is already there.

    Shared by Codex and Grok Build, which accept the same block. The timeouts are
    passed pre-formatted because the two disagree on type: Codex takes TOML floats,
    while Grok deserializes into `Option<u64>` and fails on anything but an integer.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    args_toml = ", ".join(f'"{a}"' for a in _serve_args(project_root, roots))
    block = (
        "\n[mcp_servers.sema]\n"
        "enabled = true\n"
        f'command = "{_sema_bin()}"\n'
        f"args = [{args_toml}]\n"
        f"startup_timeout_sec = {startup_timeout}\n"
        f"tool_timeout_sec = {tool_timeout}\n"
    )

    existing = config_path.read_text() if config_path.exists() else ""
    if "[mcp_servers.sema]" in existing:
        return False, config_path  # already present

    config_path.write_text(existing.rstrip() + block)
    return True, config_path


def _codex_config_add(project_root: Path, roots: list[Path] | None = None) -> tuple[bool, Path]:
    """Write [mcp_servers.sema] into <project>/.codex/config.toml. Returns (changed, config_path).

    Uses project-level config (not ~/.codex/config.toml) so the hardcoded project
    path is correct — Codex does not support {workspace_folder} template substitution.
    In multi-project mode (roots given) the block serves every project under the roots.
    """
    return _toml_mcp_config_add(
        project_root / ".codex" / "config.toml",
        project_root,
        roots,
        startup_timeout="15.0",
        tool_timeout="60.0",
    )


def _grok_config_add(project_root: Path, roots: list[Path] | None = None) -> tuple[bool, Path]:
    """Write [mcp_servers.sema] into <project>/.grok/config.toml. Returns (changed, config_path).

    Grok loads .grok/config.toml from every directory between cwd and the git root,
    and [mcp_servers] is one of the few sections it honours there, so the project
    path is baked in exactly as for Codex. Timeouts must be TOML integers.
    """
    return _toml_mcp_config_add(
        project_root / ".grok" / "config.toml",
        project_root,
        roots,
        startup_timeout="30",
        tool_timeout="60",
    )


def _toml_mcp_config_remove(config_path: Path) -> bool:
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


def _opencode_config_add(project_root: Path, roots: list[Path] | None = None) -> tuple[bool, Path]:
    """Write mcp.sema into <project>/opencode.json. Returns (changed, config_path).

    Uses project-level config so the hardcoded project path is correct — like Codex,
    opencode has no {workspace_folder} substitution. In multi-project mode (roots given)
    the block serves every project under the roots.
    """
    import sys
    sema_bin = shutil.which("sema") or str(Path(sys.executable).parent / "sema")
    config_path = project_root / "opencode.json"

    data: dict = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            return False, config_path  # don't clobber malformed user config

    mcp = data.setdefault("mcp", {})
    if "sema" in mcp:
        return False, config_path  # already present

    data.setdefault("$schema", "https://opencode.ai/config.json")
    mcp["sema"] = {
        "type": "local",
        "command": [sema_bin, *_serve_args(project_root, roots)],
        "enabled": True,
    }
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return True, config_path


def _opencode_config_remove(config_path: Path) -> bool:
    """Remove mcp.sema from opencode.json. Returns True if removed."""
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return False
    if "sema" not in data.get("mcp", {}):
        return False
    del data["mcp"]["sema"]
    if not data["mcp"]:
        del data["mcp"]
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def _cursor_config_add(project_root: Path, roots: list[Path] | None = None) -> tuple[bool, Path]:
    """Write mcpServers.sema into <project>/.cursor/mcp.json. Returns (changed, config_path).

    Cursor uses the .mcp.json standard shape — a top-level "mcpServers" map whose stdio
    entries are {command, args}. Cursor does support ${workspaceFolder} substitution, but
    the absolute project path is baked in anyway to stay consistent with the other clients
    and keep status/doctor able to introspect it; multi-project (--root) can't template it.
    """
    import sys
    sema_bin = shutil.which("sema") or str(Path(sys.executable).parent / "sema")
    config_path = project_root / ".cursor" / "mcp.json"

    data: dict = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            return False, config_path  # don't clobber malformed user config

    servers = data.setdefault("mcpServers", {})
    if "sema" in servers:
        return False, config_path  # already present

    args = _serve_args(project_root, roots)
    servers["sema"] = {"command": sema_bin, "args": args}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return True, config_path


def _cursor_config_remove(config_path: Path) -> bool:
    """Remove mcpServers.sema from .cursor/mcp.json. Returns True if removed."""
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return False
    if "sema" not in data.get("mcpServers", {}):
        return False
    del data["mcpServers"]["sema"]
    if not data["mcpServers"]:
        del data["mcpServers"]
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def _cursor_installed() -> bool:
    """True if Cursor appears installed. Cursor is a GUI editor, not a PATH CLI, so its
    presence is the ~/.cursor config dir (created on first run) rather than `which`.
    An optional `cursor` shell shim, if the user installed one, also counts."""
    return (Path.home() / ".cursor").is_dir() or shutil.which("cursor") is not None


def _cursor_registered(config_path: Path) -> bool:
    """True if .cursor/mcp.json exists and defines the sema MCP server."""
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return "sema" in data.get("mcpServers", {})


@main.command()
@click.option("--uninstall", is_flag=True, help="Remove sema from the selected client")
@click.option("--codex", "target", flag_value="codex", help="Register with OpenAI Codex")
@click.option("--grok", "target", flag_value="grok", help="Register with Grok Build")
@click.option("--cursor", "target", flag_value="cursor", help="Register with Cursor")
@click.option("--claude", "target", flag_value="claude", default=True, help="Register with Claude Code (default)")
@click.option("--root", "roots", multiple=True, type=click.Path(exists=True),
              help="Serve every indexed project under this directory (repeatable). Enables multi-project mode.")
def init(uninstall: bool, target: str, roots: tuple[str, ...]):
    """Register sema as an MCP server with Claude Code, OpenAI Codex, Grok Build, or Cursor.

    Single-project by default (the current directory). Pass one or more --root
    directories to serve every indexed project found beneath them at once.
    """
    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR
    root_paths = [Path(r).resolve() for r in roots]

    if target == "codex":
        _init_codex(uninstall, project_root, index_path, root_paths)
    elif target == "grok":
        _init_grok(uninstall, project_root, index_path, root_paths)
    elif target == "cursor":
        _init_cursor(uninstall, project_root, index_path, root_paths)
    else:
        _init_claude(uninstall, project_root, index_path, root_paths)


def _report_discovered(roots: list[Path]) -> None:
    """Print how many indexed projects the given roots currently cover."""
    from .mcp.registry import discover_projects
    discovered = discover_projects(roots)
    if discovered:
        console.print(f"[dim]  Found {len(discovered)} indexed project(s) under the root(s):[/dim]")
        for pr, _ip in discovered:
            console.print(f"[dim]    • {pr}[/dim]")
    else:
        console.print("[yellow]  No indexed projects found yet — run [bold]sema index .[/bold] inside each project.[/yellow]")


def _install_navigation_skills(
    project_root: Path,
    roots: list[Path],
    providers: set[str],
) -> None:
    """Install the portable sema skill in one project or every discovered project."""
    if not providers:
        return
    from .mcp.registry import discover_projects
    from .skills import install_provider_skills

    projects = [p for p, _index in discover_projects(roots)] if roots else [project_root]
    for project in projects:
        for result in install_provider_skills(project, providers):
            relative = result.path.relative_to(project)
            if result.status == "installed":
                console.print(f"[green]✔[/green] Installed sema skill: {project / relative}")
            elif result.status == "updated":
                console.print(f"[green]✔[/green] Updated sema skill: {project / relative}")
            elif result.status == "preserved":
                console.print(
                    f"[yellow]–[/yellow] Preserved customized skill: {project / relative}"
                )


def _init_claude(uninstall: bool, project_root: Path, index_path: Path, roots: list[Path]) -> None:
    import subprocess
    if uninstall:
        ok = _claude_mcp_remove(scope="user")
        if ok:
            console.print("[yellow]✔[/yellow] Removed sema MCP server")
        else:
            console.print("[red]✗[/red] Could not remove via 'claude mcp remove'. Is the claude CLI installed?")

        try:
            # Match both single-project and multi-project (--root) serve processes.
            result = subprocess.run(
                ["pgrep", "-f", "sema serve"],
                capture_output=True, text=True,
            )
            pids = result.stdout.split()
            if pids:
                subprocess.run(["kill"] + pids, check=False)
                console.print(f"[yellow]✔[/yellow] Stopped {len(pids)} sema serve process(es)")
        except FileNotFoundError:
            pass
        return

    if roots:
        _report_discovered(roots)
    elif not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    ok = _claude_mcp_add(project_root=project_root, roots=roots or None, scope="user")
    if ok:
        if roots:
            console.print("[green]✔[/green] Registered as MCP server 'sema' (user scope, multi-project)")
        else:
            console.print("[green]✔[/green] Registered as MCP server 'sema' (user scope)")
        _install_navigation_skills(project_root, roots, {"claude"})
        console.print("\n[bold]Done.[/bold] Run [bold]/mcp[/bold] in Claude Code to confirm.")
    else:
        manual = " ".join(_serve_args(project_root, roots or None))
        console.print("[red]✗[/red] Could not register via 'claude mcp add'. Is the claude CLI installed?")
        console.print(f"\nRun manually:\n  claude mcp add sema -s user -- sema {manual}")


def _init_codex(uninstall: bool, project_root: Path, index_path: Path, roots: list[Path]) -> None:
    config_path = project_root / ".codex" / "config.toml"
    if uninstall:
        removed = _toml_mcp_config_remove(config_path)
        if removed:
            console.print(f"[yellow]✔[/yellow] Removed \\[mcp_servers.sema] from {config_path}")
        else:
            console.print(f"[yellow]–[/yellow] \\[mcp_servers.sema] not found in {config_path}")
        return

    if roots:
        _report_discovered(roots)
    elif not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    changed, config_path = _codex_config_add(project_root, roots=roots or None)
    if changed:
        mode = "project scope, multi-project" if roots else "project scope"
        console.print(f"[green]✔[/green] Registered as MCP server 'sema' ({mode})")
        console.print(f"[dim]  {config_path}[/dim]")
    else:
        console.print(f"[yellow]–[/yellow] Already registered in {config_path}")
    _install_navigation_skills(project_root, roots, {"codex"})
    console.print("\n[bold]Done.[/bold] Run [bold]/mcp[/bold] in Codex to confirm.")


def _init_grok(uninstall: bool, project_root: Path, index_path: Path, roots: list[Path]) -> None:
    config_path = project_root / ".grok" / "config.toml"
    if uninstall:
        removed = _toml_mcp_config_remove(config_path)
        if removed:
            console.print(f"[yellow]✔[/yellow] Removed \\[mcp_servers.sema] from {config_path}")
        else:
            console.print(f"[yellow]–[/yellow] \\[mcp_servers.sema] not found in {config_path}")
        return

    if roots:
        _report_discovered(roots)
    elif not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    changed, config_path = _grok_config_add(project_root, roots=roots or None)
    if changed:
        mode = "project scope, multi-project" if roots else "project scope"
        console.print(f"[green]✔[/green] Registered as MCP server 'sema' ({mode})")
        console.print(f"[dim]  {config_path}[/dim]")
    else:
        console.print(f"[yellow]–[/yellow] Already registered in {config_path}")
    _install_navigation_skills(project_root, roots, {"grok"})
    # Grok refuses to start project-scoped servers in an untrusted folder, so a
    # successful write alone doesn't mean sema will load. Say so here — otherwise
    # the first symptom is sema silently missing from /mcps.
    console.print("\n[bold]Done.[/bold] Run [bold]grok[/bold] in this project and accept the trust prompt,")
    console.print("then [bold]/mcps[/bold] to confirm. Check any time with: [bold]grok mcp doctor sema[/bold]")


def _init_cursor(uninstall: bool, project_root: Path, index_path: Path, roots: list[Path]) -> None:
    config_path = project_root / ".cursor" / "mcp.json"
    if uninstall:
        removed = _cursor_config_remove(config_path)
        if removed:
            console.print(f"[yellow]✔[/yellow] Removed sema from {config_path}")
        else:
            console.print(f"[yellow]–[/yellow] sema not found in {config_path}")
        return

    if roots:
        _report_discovered(roots)
    elif not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    changed, config_path = _cursor_config_add(project_root, roots=roots or None)
    if changed:
        mode = "project scope, multi-project" if roots else "project scope"
        console.print(f"[green]✔[/green] Registered as MCP server 'sema' ({mode})")
        console.print(f"[dim]  {config_path}[/dim]")
    else:
        console.print(f"[yellow]–[/yellow] Already registered in {config_path}")
    _install_navigation_skills(project_root, roots, {"cursor"})
    console.print("\n[bold]Done.[/bold] Reload Cursor, then enable sema under")
    console.print("[bold]Settings → MCP[/bold] (Cursor asks to approve a newly added server once).")


@main.command()
@click.option("--uninstall", is_flag=True, help="Remove sema from every detected AI CLI")
@click.option("--skip-claude", is_flag=True, help="Do not touch Claude Code")
@click.option("--skip-codex", is_flag=True, help="Do not touch OpenAI Codex")
@click.option("--skip-opencode", is_flag=True, help="Do not touch opencode")
@click.option("--skip-grok", is_flag=True, help="Do not touch Grok Build")
@click.option("--skip-cursor", is_flag=True, help="Do not touch Cursor")
@click.option("--root", "roots", multiple=True, type=click.Path(exists=True),
              help="Serve every indexed project under this directory (repeatable). Enables multi-project mode.")
def setup(uninstall: bool, skip_claude: bool, skip_codex: bool, skip_opencode: bool,
          skip_grok: bool, skip_cursor: bool, roots: tuple[str, ...]):
    """Detect installed AI clients and register sema with each in one shot.

    The one-command counterpart to `sema init`: instead of registering with a
    single client, it discovers which of Claude Code, Codex, opencode, Grok Build,
    and Cursor are installed and wires sema into each. Idempotent and safe to re-run.
    Skip any client with --skip-<name>; env vars SEMA_SKIP_CLAUDE / SEMA_SKIP_CODEX /
    SEMA_SKIP_OPENCODE / SEMA_SKIP_GROK / SEMA_SKIP_CURSOR (set by the installer) are
    honoured too.
    """
    import os

    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR
    root_paths = [Path(r).resolve() for r in roots]

    skip_claude = skip_claude or os.environ.get("SEMA_SKIP_CLAUDE") == "1"
    skip_codex = skip_codex or os.environ.get("SEMA_SKIP_CODEX") == "1"
    skip_opencode = skip_opencode or os.environ.get("SEMA_SKIP_OPENCODE") == "1"
    skip_grok = skip_grok or os.environ.get("SEMA_SKIP_GROK") == "1"
    skip_cursor = skip_cursor or os.environ.get("SEMA_SKIP_CURSOR") == "1"

    # Every project-scoped client (codex, opencode, grok, cursor) needs an index present
    # unless we're serving whole roots. Claude is user-scoped and checked the same way.
    if not uninstall and not root_paths and not index_path.exists():
        console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    if not uninstall and root_paths:
        _report_discovered(root_paths)

    claude_bin = _find_claude_bin()
    codex_bin = shutil.which("codex")
    opencode_bin = shutil.which("opencode")
    grok_bin = shutil.which("grok")
    cursor_present = _cursor_installed()

    console.print()
    verb = "Removing" if uninstall else "Registering"
    console.print(f"[bold]{verb} sema[/bold]")

    any_client = False
    skill_providers: set[str] = set()

    # ── Claude Code (user scope) ──────────────────────────────────────────────
    if skip_claude:
        console.print("  Claude Code  [dim]– skipped[/dim]")
    elif not claude_bin:
        console.print("  Claude Code  [dim]– CLI not found[/dim]")
    else:
        any_client = True
        if uninstall:
            ok = _claude_mcp_remove(scope="user")
            console.print(f"  Claude Code  {'[yellow]✔ removed[/yellow]' if ok else '[red]✗ failed[/red]'}")
        else:
            ok = _claude_mcp_add(project_root=project_root, roots=root_paths or None, scope="user")
            console.print(f"  Claude Code  {'[green]✔ registered[/green]' if ok else '[red]✗ failed[/red]'}")
            if ok:
                skill_providers.add("claude")

    # ── Codex (project scope) ─────────────────────────────────────────────────
    if skip_codex:
        console.print("  Codex        [dim]– skipped[/dim]")
    elif not codex_bin:
        console.print("  Codex        [dim]– CLI not found[/dim]")
    else:
        any_client = True
        codex_cfg = project_root / ".codex" / "config.toml"
        if uninstall:
            removed = _toml_mcp_config_remove(codex_cfg)
            console.print(f"  Codex        {'[yellow]✔ removed[/yellow]' if removed else '[dim]– nothing to remove[/dim]'}")
        else:
            changed, _cfg = _codex_config_add(project_root, roots=root_paths or None)
            console.print(f"  Codex        {'[green]✔ registered[/green]' if changed else '[yellow]– already present[/yellow]'}")
            skill_providers.add("codex")

    # ── opencode (project scope) ──────────────────────────────────────────────
    if skip_opencode:
        console.print("  opencode     [dim]– skipped[/dim]")
    elif not opencode_bin:
        console.print("  opencode     [dim]– CLI not found[/dim]")
    else:
        any_client = True
        opencode_cfg = project_root / "opencode.json"
        if uninstall:
            removed = _opencode_config_remove(opencode_cfg)
            console.print(f"  opencode     {'[yellow]✔ removed[/yellow]' if removed else '[dim]– nothing to remove[/dim]'}")
        else:
            changed, _cfg = _opencode_config_add(project_root, roots=root_paths or None)
            console.print(f"  opencode     {'[green]✔ registered[/green]' if changed else '[yellow]– already present[/yellow]'}")
            skill_providers.add("opencode")

    # ── Grok Build (project scope) ────────────────────────────────────────────
    if skip_grok:
        console.print("  Grok Build   [dim]– skipped[/dim]")
    elif not grok_bin:
        console.print("  Grok Build   [dim]– CLI not found[/dim]")
    else:
        any_client = True
        grok_cfg = project_root / ".grok" / "config.toml"
        if uninstall:
            removed = _toml_mcp_config_remove(grok_cfg)
            console.print(f"  Grok Build   {'[yellow]✔ removed[/yellow]' if removed else '[dim]– nothing to remove[/dim]'}")
        else:
            changed, _cfg = _grok_config_add(project_root, roots=root_paths or None)
            console.print(f"  Grok Build   {'[green]✔ registered[/green]' if changed else '[yellow]– already present[/yellow]'}")
            skill_providers.add("grok")

    # ── Cursor (project scope) ────────────────────────────────────────────────
    if skip_cursor:
        console.print("  Cursor       [dim]– skipped[/dim]")
    elif not cursor_present:
        console.print("  Cursor       [dim]– not installed[/dim]")
    else:
        any_client = True
        cursor_cfg = project_root / ".cursor" / "mcp.json"
        if uninstall:
            removed = _cursor_config_remove(cursor_cfg)
            console.print(f"  Cursor       {'[yellow]✔ removed[/yellow]' if removed else '[dim]– nothing to remove[/dim]'}")
        else:
            changed, _cfg = _cursor_config_add(project_root, roots=root_paths or None)
            console.print(f"  Cursor       {'[green]✔ registered[/green]' if changed else '[yellow]– already present[/yellow]'}")
            skill_providers.add("cursor")

    if not uninstall:
        _install_navigation_skills(project_root, root_paths, skill_providers)

    console.print()
    if not any_client and not uninstall:
        console.print("[yellow]No supported AI clients detected.[/yellow] Install Claude Code, Codex, opencode, Grok Build, or Cursor, then re-run [bold]sema setup[/bold].")
    elif not uninstall:
        console.print("[bold]Done.[/bold] Run [bold]/mcp[/bold] in your AI client to confirm, or [bold]sema doctor[/bold] to diagnose.")


@main.command()
@click.argument("query")
@click.option("--top-k", default=5, help="Number of results")
@click.option("--all-types", is_flag=True, help="Include docs/config sections (default: code only)")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON (for editors/scripts)")
def search(query: str, top_k: int, all_types: bool, as_json: bool):
    """Search the codebase index. Useful for testing without Claude."""
    from .store.chroma import SemaStore
    from .store.bm25 import BM25Index
    from .indexer.embedder import Embedder
    from .mcp.tools import _CODE_CHUNK_TYPES, _rrf_merge

    project_root = Path(".").resolve()
    index_path = project_root / DEFAULT_INDEX_DIR

    if not index_path.exists():
        if as_json:
            _emit_json({"error": "no_index", "message": "No index found. Run `sema index .` first."})
        else:
            console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    store = SemaStore(index_path)
    embedder = Embedder()

    top_k = max(1, min(top_k, 10))
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

    if as_json:
        _emit_json({
            "query": query,
            "results": [
                {
                    "file": r["file"],
                    "name": r["name"],
                    "type": r["type"],
                    "signature": r["signature"],
                    "start_line": r["start_line"],
                    "score": round(float(r["score"]), 4),
                }
                for r in results
            ],
        })
        return

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


@main.command(name="_query-server", hidden=True)
@click.option("--project", default=".", type=click.Path(exists=True))
def query_server(project: str):
    """Run the private persistent JSONL query worker used by editor clients."""
    from .query_server import serve_query_worker

    serve_query_worker(Path(project))


@main.command()
@click.argument("symbol")
@click.option("--project", default=".", type=click.Path(exists=True), help="Project to read from (default: current directory)")
@click.option("--json", "as_json", is_flag=True, help="Output the source as JSON (for editors/scripts)")
def get(symbol: str, project: str, as_json: bool):
    """Print the full source of a function/class/method by name.

    The CLI equivalent of the get_code MCP tool — returns every implementation
    that matches `symbol` (e.g. a controller method and a service method with
    the same name).
    """
    from .store.chroma import SemaStore

    project_root = Path(project).resolve()
    index_path = project_root / DEFAULT_INDEX_DIR
    if not index_path.exists():
        if as_json:
            _emit_json({"error": "no_index", "message": "No index found. Run `sema index .` first."})
        else:
            console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    store = SemaStore(index_path)
    results = store.get_by_name(symbol)

    if as_json:
        _emit_json({
            "symbol": symbol,
            "implementations": [
                {
                    "file": r["file"],
                    "type": r["chunk_type"],
                    "start_line": r["start_line"],
                    "end_line": r["end_line"],
                    "body": r["body"],
                }
                for r in results
            ],
        })
        return

    if not results:
        console.print(f"[yellow]Symbol '{symbol}' not found in index.[/yellow]")
        return
    for r in results:
        console.print(f"[dim]// {r['file']} ({r['chunk_type']}) — lines {r['start_line']}-{r['end_line']}[/dim]")
        console.print(r["body"])
        console.print()


@main.command()
@click.argument("description")
@click.option("--project", default=".", type=click.Path(exists=True), help="Project to check (default: current directory)")
@click.option("--json", "as_json", is_flag=True, help="Output the verdict as JSON (for editors/scripts)")
def reuse(description: str, project: str, as_json: bool):
    """Check whether functionality already exists before building it.

    Grounds the "reuse before you write" principle in the index: describe what
    you're about to build and sema tells you to reuse, review, or safely build.
    """
    from .store.chroma import SemaStore
    from .indexer.embedder import Embedder
    from .reuse import assess_reuse, ReuseVerdict

    project_root = Path(project).resolve()
    index_path = project_root / DEFAULT_INDEX_DIR
    if not index_path.exists():
        if as_json:
            _emit_json({"error": "no_index", "message": "No index found. Run `sema index .` first."})
        else:
            console.print("[red]✗[/red] No index found. Run [bold]sema index .[/bold] first.")
        return

    store = SemaStore(index_path)
    embedder = Embedder()
    result = assess_reuse(store, embedder, description)

    if as_json:
        _emit_json({
            "description": description,
            "verdict": result.verdict.value,
            "top_score": round(float(result.top_score), 4),
            "candidates": [
                {
                    "file": h["file"],
                    "name": h["name"],
                    "type": h["type"],
                    "signature": h["signature"],
                    "start_line": h["start_line"],
                    "score": round(float(h["score"]), 4),
                }
                for h in result.candidates
            ],
        })
        return

    color = {
        ReuseVerdict.EXISTS: "red",
        ReuseVerdict.RELATED: "yellow",
        ReuseVerdict.NOVEL: "green",
    }[result.verdict]
    label = {
        ReuseVerdict.EXISTS: "ALREADY EXISTS — reuse or extend",
        ReuseVerdict.RELATED: "RELATED code exists — review first",
        ReuseVerdict.NOVEL: "NOVEL — safe to build",
    }[result.verdict]

    console.print()
    console.print(f"[bold]{description}[/bold]")
    console.print(f"  Verdict  [{color}]{label}[/{color}]  [dim](top {int(round(result.top_score*100))}%)[/dim]\n")
    for h in result.candidates:
        console.print(
            f"  [cyan]{h['file']}::{h['name']}[/cyan]  "
            f"[dim]line {h['start_line']}[/dim]  "
            f"[green]{int(round(h['score']*100))}% match[/green]"
        )
        console.print(f"    [dim]{h['type']}:[/dim] {h['signature']}")
    if not result.candidates:
        console.print("  [dim]No existing implementation found — prefer stdlib/existing deps, keep it minimal.[/dim]")


def _detect_index_changes(project_root: Path) -> tuple[int, int]:
    """Return (changed_or_new, deleted) files relative to the stored hash set."""
    from .store.hashes import FileHashStore
    from .utils.file_walker import walk_project

    hashes = FileHashStore(project_root / ".sema")
    files = list(walk_project(project_root))
    current = {str(path.relative_to(project_root)): path for path in files}
    changed = sum(
        not hashes.is_unchanged(relative, path)
        for relative, path in current.items()
    )
    deleted = len(hashes.known_paths() - current.keys())
    return changed, deleted


def _emit_status_json(project_root: Path, meta_path: Path) -> None:
    """Emit index + registration status as JSON. Cheap — no subprocesses."""
    from datetime import datetime, timezone

    index_path = project_root / DEFAULT_INDEX_DIR
    index: dict = {"exists": meta_path.exists(), "project": str(project_root)}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        chunks = meta.get("chunk_count")
        files = meta.get("file_count")
        try:
            from .store.chroma import SemaStore
            all_meta = SemaStore(index_path).get_all_metadata()
            chunks = len(all_meta)
            files = len({m.get("file", "") for m in all_meta if m.get("file")})
        except Exception:
            pass
        indexed_at = meta.get("indexed_at")
        age_days = None
        stale = False
        changed_files = 0
        deleted_files = 0
        if indexed_at:
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(indexed_at.replace("Z", "+00:00"))
                age_days = age.days
                stale = age.days > 7
            except Exception:
                pass
        try:
            changed_files, deleted_files = _detect_index_changes(project_root)
            stale = stale or changed_files > 0 or deleted_files > 0
        except Exception:
            # Status remains useful even if one file becomes unreadable.
            pass
        index.update({
            "chunks": chunks,
            "files": files,
            "indexed_at": indexed_at,
            "model": meta.get("model"),
            "age_days": age_days,
            "stale": stale,
            "changed_files": changed_files,
            "deleted_files": deleted_files,
        })

    claude_registered = False
    claude_cfg = Path.home() / ".claude.json"
    if claude_cfg.exists():
        try:
            claude_registered = "sema" in json.loads(claude_cfg.read_text()).get("mcpServers", {})
        except Exception:
            pass
    def _toml_registered(config_path: Path) -> bool:
        if not config_path.exists():
            return False
        try:
            return "[mcp_servers.sema]" in config_path.read_text()
        except Exception:
            return False

    codex_registered = _toml_registered(project_root / ".codex" / "config.toml")
    grok_registered = _toml_registered(project_root / ".grok" / "config.toml")
    cursor_registered = _cursor_registered(project_root / ".cursor" / "mcp.json")

    _emit_json({
        "index": index,
        "registration": {
            "claude": claude_registered,
            "codex": codex_registered,
            "grok": grok_registered,
            "cursor": cursor_registered,
        },
    })


def _open_index(index_root: Path):
    """Open the store for a project root, or None if no index exists there."""
    from .store.chroma import SemaStore
    index_path = index_root / DEFAULT_INDEX_DIR
    if not index_path.exists():
        return None
    return SemaStore(index_path)


def _refresh_meta(index_root: Path, store) -> None:
    """Rewrite meta.json counts + timestamp after an incremental add/remove."""
    import datetime
    meta_path = index_root / DEFAULT_META_FILE
    try:
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    except Exception:
        meta = {}
    all_meta = store.get_all_metadata()
    meta["chunk_count"] = len(all_meta)
    meta["file_count"] = len({m.get("file", "") for m in all_meta if m.get("file")})
    meta["indexed_at"] = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    meta.setdefault("model", "all-MiniLM-L6-v2")
    meta.setdefault("version", "1")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))


@main.command(name="list")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for editors/scripts)")
def list_cmd(path: str, as_json: bool):
    """List indexed files and the symbols under each (a map of the index)."""
    index_root = Path(path).resolve()
    store = _open_index(index_root)
    if store is None:
        if as_json:
            _emit_json({"files": [], "chunk_count": 0, "file_count": 0})
        else:
            console.print("[yellow]No index found[/yellow] — run [bold]sema index .[/bold]")
        return

    by_file: dict[str, dict] = {}
    for m in store.get_all_metadata():
        f = m.get("file", "")
        if not f:
            continue
        entry = by_file.setdefault(f, {"file": f, "language": m.get("language", ""), "chunks": []})
        entry["chunks"].append({
            "name": m.get("name", ""),
            "type": m.get("chunk_type", ""),
            "start_line": m.get("start_line", 0),
            "end_line": m.get("end_line", 0),
            "signature": m.get("signature", ""),
        })
    files = sorted(by_file.values(), key=lambda e: e["file"])
    for e in files:
        e["chunks"].sort(key=lambda s: s["start_line"])

    if as_json:
        _emit_json({
            "files": files,
            "chunk_count": sum(len(e["chunks"]) for e in files),
            "file_count": len(files),
        })
    else:
        for e in files:
            console.print(f"[bold]{e['file']}[/bold] [dim]({len(e['chunks'])})[/dim]")
            for c in e["chunks"]:
                console.print(f"    [dim]{c['type']}[/dim] {c['name']}  [dim]:{c['start_line']}[/dim]")


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--root", default=".", type=click.Path(exists=True), help="Project root that owns the index")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for editors/scripts)")
def add(file: str, root: str, as_json: bool):
    """Add or re-index a single file in the index."""
    from .indexer.chunker import index_file
    from .indexer.embedder import Embedder
    from .store.chroma import SemaStore
    from .store.hashes import FileHashStore

    index_root = Path(root).resolve()
    file_path = Path(file).resolve()
    try:
        rel = str(file_path.relative_to(index_root))
    except ValueError:
        msg = f"{file_path} is not inside {index_root}"
        if as_json:
            _emit_json({"ok": False, "error": msg})
        else:
            console.print(f"[red]✗[/red] {msg}")
        return

    index_path = index_root / DEFAULT_INDEX_DIR
    store = SemaStore(index_path)
    embedder = Embedder()
    hash_store = FileHashStore(index_path.parent)
    n = index_file(file_path, index_root, store, embedder, base_root=index_root, hash_store=hash_store)
    _refresh_meta(index_root, store)
    if as_json:
        _emit_json({"ok": True, "file": rel, "chunks": n})
    else:
        console.print(f"[green]✔[/green] Indexed [bold]{rel}[/bold] — {n} chunks")


@main.command()
@click.argument("file")
@click.option("--root", default=".", type=click.Path(exists=True), help="Project root that owns the index")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for editors/scripts)")
def remove(file: str, root: str, as_json: bool):
    """Remove a file from the index (does not delete the file on disk)."""
    from .store.hashes import FileHashStore

    index_root = Path(root).resolve()
    # Accept either the stored relative path or a real/absolute path under root.
    p = Path(file)
    rel = file
    if p.is_absolute() or p.exists():
        try:
            rel = str(p.resolve().relative_to(index_root))
        except ValueError:
            rel = file

    store = _open_index(index_root)
    if store is None:
        if as_json:
            _emit_json({"ok": False, "error": "no index"})
        else:
            console.print("[yellow]No index found[/yellow]")
        return
    store.delete_by_file(rel)
    hash_store = FileHashStore((index_root / DEFAULT_INDEX_DIR).parent)  # the .sema dir
    hash_store.remove(rel)
    hash_store.save()
    _refresh_meta(index_root, store)
    if as_json:
        _emit_json({"ok": True, "file": rel})
    else:
        console.print(f"[green]✔[/green] Removed [bold]{rel}[/bold] from the index")


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Emit JSON: {redacted, text, entities} or an {error} payload")
def redact(as_json: bool):
    """Redact person/location names from text read on STDIN (spaCy NER).

    Reads text from STDIN — never pass sensitive data as a CLI argument, where it
    would leak into the process list and shell history. This is the model half of
    the extension's hybrid PII redaction (structured PII / secrets are handled
    client-side with regex). Requires the optional PII extra: `pip install
    'sema-mcp[pii]'` plus `python -m spacy download en_core_web_sm`.
    """
    import sys
    from .redact import redact_text, RedactionUnavailable

    text = sys.stdin.read()
    try:
        result = redact_text(text)
    except RedactionUnavailable as e:
        # Degrade gracefully: report unavailability and echo the input unchanged so
        # the caller can fall back to regex-only redaction.
        if as_json:
            _emit_json({"redacted": False, "error": "unavailable", "message": str(e), "text": text})
            return
        raise click.ClickException(str(e))

    if as_json:
        _emit_json({"redacted": True, **result})
    else:
        click.echo(result["text"])


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show full details including MCP registration and binary paths.")
@click.option("--json", "as_json", is_flag=True, help="Output status as JSON (for editors/scripts)")
def status(verbose: bool, as_json: bool):
    """Show index stats and MCP registration status."""
    import subprocess
    import shutil as _shutil

    project_root = Path(".").resolve()
    meta_path = project_root / DEFAULT_META_FILE

    if as_json:
        _emit_status_json(project_root, meta_path)
        return

    # ── Index ─────────────────────────────────────────────────────────────────
    console.print()
    index_path = project_root / DEFAULT_INDEX_DIR
    if not meta_path.exists():
        console.print("[bold]Index[/bold]  [red]✗ No index found[/red] — run [bold]sema index .[/bold]")
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

        console.print("[bold]Index[/bold]")
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
                console.print("  Languages")
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
            console.print("  [yellow]  ⚠  Serving a different project than cwd[/yellow]")
            console.print(f"  [dim]     cwd:     {project_root}[/dim]")
            console.print(f"  [dim]     serving: {serving}[/dim]")
            console.print(f"  [dim]     Fix: {fix_cmd}[/dim]")

    def _print_serving_roots(roots: list[str], project_root: Path) -> None:
        """Multi-project mode: list the served roots and the projects discovered under them."""
        from .mcp.registry import discover_projects
        console.print(f"  Serving      [green]multi-project[/green] ({len(roots)} root(s))")
        for r in roots:
            console.print(f"  [dim]     root: {r}[/dim]")
        discovered = discover_projects([Path(r) for r in roots])
        console.print(f"  Projects     {len(discovered)} indexed")
        cwd_served = any(Path(r).resolve() == project_root or
                         project_root.is_relative_to(Path(r).resolve()) for r in roots)
        for pr, _ip in discovered:
            here = " [green](cwd)[/green]" if pr == project_root else ""
            console.print(f"  [dim]     • {pr}[/dim]{here}")
        if not cwd_served:
            console.print("  [yellow]  ⚠  Current directory is not under any served root[/yellow]")

    console.print()
    console.print("[bold]MCP server[/bold]")

    # Claude Code
    claude = _shutil.which("claude")
    if claude:
        result = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True)
        output = result.stdout + result.stderr
        if "sema" in output:
            for line in output.splitlines():
                if "sema" in line and ":" in line:
                    serving = None
                    served_roots = _re.findall(r"--root\s+(\S+)", line)
                    if not served_roots and "--project" in line:
                        parts = line.split("--project")
                        if len(parts) > 1:
                            serving = parts[1].strip().split()[0]

                    if "Failed" in line:
                        console.print("  Claude Code  [red]✗ Failed[/red]")
                        console.print("  [dim]  Fix: sema init --claude --uninstall && sema init --claude[/dim]")
                    elif "Connected" in line or "✓" in line:
                        console.print("  Claude Code  [green]✔ Connected[/green]")
                    else:
                        console.print("  Claude Code  [yellow]⚠ Registered (not connected)[/yellow]")

                    if served_roots:
                        _print_serving_roots(served_roots, project_root)
                    else:
                        _print_serving(serving, project_root, "sema init --claude --uninstall && sema init --claude")

                    if verbose:
                        claude_cfg = Path.home() / ".claude.json"
                        console.print(f"  [dim]  config:  {claude_cfg}[/dim]")
                        console.print(f"  [dim]  command: {serving and f'sema serve --project {serving}'}[/dim]")
        else:
            console.print("  Claude Code  [yellow]–[/yellow] not registered — run: sema init --claude")
    else:
        console.print("  Claude Code  [dim]–[/dim] claude CLI not found")

    # Codex and Grok Build — same TOML block, different config path.
    def _print_toml_client(label: str, config_path: Path, flag: str) -> None:
        fix = f"sema init {flag} --uninstall && sema init {flag}"
        if not config_path.exists():
            console.print(f"  {label} [dim]–[/dim] not registered — run: sema init {flag}")
            return
        content = config_path.read_text()
        if "[mcp_servers.sema]" not in content:
            console.print(f"  {label} [yellow]–[/yellow] not registered — run: sema init {flag}")
            return

        serving = None
        served_roots = _re.findall(r'"--root",\s*"([^"]+)"', content)
        if not served_roots:
            m = _re.search(r'"--project",\s*"([^"]+)"', content)
            if m:
                serving = m.group(1)

        # Check if binary in config exists
        cmd_ok = True
        cm = _re.search(r'^command\s*=\s*"([^"]+)"', content, _re.MULTILINE)
        if cm and not Path(cm.group(1)).exists():
            console.print(f"  {label} [red]✗ Failed[/red]")
            console.print(f"  [dim]  Binary not found: {cm.group(1)}[/dim]")
            console.print(f"  [dim]  Fix: {fix}[/dim]")
            cmd_ok = False

        if cmd_ok:
            console.print(f"  {label} [green]✔ Connected[/green]")

        if served_roots:
            _print_serving_roots(served_roots, project_root)
        else:
            _print_serving(serving, project_root, fix)

        if verbose:
            console.print(f"  [dim]  config:  {config_path}[/dim]")

    _print_toml_client("Codex       ", project_root / ".codex" / "config.toml", "--codex")
    _print_toml_client("Grok Build  ", project_root / ".grok" / "config.toml", "--grok")

    # Cursor — same idea, JSON config (.cursor/mcp.json → mcpServers.sema).
    def _print_cursor(label: str, config_path: Path) -> None:
        flag = "--cursor"
        fix = f"sema init {flag} --uninstall && sema init {flag}"
        entry = None
        if config_path.exists():
            try:
                entry = json.loads(config_path.read_text()).get("mcpServers", {}).get("sema")
            except (json.JSONDecodeError, OSError):
                entry = None
        if not entry:
            console.print(f"  {label} [dim]–[/dim] not registered — run: sema init {flag}")
            return

        args = entry.get("args", [])
        served_roots = [args[i + 1] for i, a in enumerate(args) if a == "--root" and i + 1 < len(args)]
        serving = None
        if not served_roots and "--project" in args:
            serving = args[args.index("--project") + 1] if args.index("--project") + 1 < len(args) else None

        binary = entry.get("command", "")
        if binary and not Path(binary).exists():
            console.print(f"  {label} [red]✗ Failed[/red]")
            console.print(f"  [dim]  Binary not found: {binary}[/dim]")
            console.print(f"  [dim]  Fix: {fix}[/dim]")
        else:
            console.print(f"  {label} [green]✔ Connected[/green]")

        if served_roots:
            _print_serving_roots(served_roots, project_root)
        else:
            _print_serving(serving, project_root, fix)

        if verbose:
            console.print(f"  [dim]  config:  {config_path}[/dim]")

    _print_cursor("Cursor      ", project_root / ".cursor" / "mcp.json")

    # Binary
    if verbose:
        console.print()
        console.print("[bold]Binary[/bold]")
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
@click.option("--project", default=None, type=click.Path(exists=True),
              help="Serve a single project (default: current directory).")
@click.option("--root", "roots", multiple=True, type=click.Path(exists=True),
              help="Serve every indexed project found under this directory (repeatable). Enables multi-project mode.")
def serve(project: str | None, roots: tuple[str, ...]):
    """Start MCP server (called automatically by Claude Code / Codex).

    Single-project mode with --project (or the current directory), or
    multi-project mode with one or more --root directories.
    """
    from .mcp.server import serve as _serve, serve_roots as _serve_roots
    if roots:
        _serve_roots([Path(r).resolve() for r in roots])
    else:
        project_root = Path(project or ".").resolve()
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
    project_root = Path(".").resolve()

    # ── 1. Binary ────────────────────────────────────────────────────────────
    binary = shutil.which("sema")
    console.print("\n[bold]1. Binary[/bold]")
    if binary:
        console.print(f"  [green]✔[/green] Found: {binary}")
    else:
        console.print("  [red]✗[/red] sema not found on PATH")
        console.print("  [dim]  Fix: add sema's .venv/bin to PATH, then source ~/.zshrc[/dim]")
        ok = False

    # ── 2. Venv mismatch ─────────────────────────────────────────────────────
    console.print("\n[bold]2. Python environment[/bold]")
    python = sys.executable
    console.print(f"  [dim]  Python: {python}[/dim]")
    if binary:
        binary_venv = _launcher_environment(binary)
        # A uv environment's `bin/python` may itself point to uv's shared managed
        # interpreter. Resolve the environment directory, not that final symlink.
        python_venv = Path(python).parent.parent.resolve()
        if binary_venv == python_venv:
            console.print("  [green]✔[/green] Binary and Python are in the same venv")
        else:
            console.print("  [red]✗[/red] Venv mismatch")
            console.print(f"  [dim]  Binary:  {binary_venv}[/dim]")
            console.print(f"  [dim]  Python:  {python_venv}[/dim]")
            console.print("  [dim]  Fix: re-register after confirming `which sema` is correct[/dim]")
            ok = False

    # ── 3. Package importable ────────────────────────────────────────────────
    console.print("\n[bold]3. Package[/bold]")
    try:
        import sema  # noqa: F401
        console.print("  [green]✔[/green] sema package importable")
    except ImportError:
        console.print("  [red]✗[/red] sema package not installed in this venv")
        console.print("  [dim]  Fix: cd /path/to/sema && uv pip install -e '.[dev]'[/dim]")
        ok = False

    # ── 4. Claude Code registration ──────────────────────────────────────────
    import re as _re
    console.print("\n[bold]4. Claude Code registration[/bold]")
    claude_cfg = Path.home() / ".claude.json"
    console.print(f"  [dim]  config: {claude_cfg}[/dim]")
    claude = shutil.which("claude")

    # Read config directly — more reliable than parsing `claude mcp list` output
    claude_entry: dict | None = None
    if claude_cfg.exists():
        try:
            claude_data = json.loads(claude_cfg.read_text())
            claude_entry = claude_data.get("mcpServers", {}).get("sema")
        except Exception:
            pass

    if claude_entry:
        reg_binary = claude_entry.get("command", "")
        reg_args   = claude_entry.get("args", [])
        reg_project = None
        for i, a in enumerate(reg_args):
            if a == "--project" and i + 1 < len(reg_args):
                reg_project = reg_args[i + 1]

        console.print(f"  [dim]  binary:  {reg_binary}[/dim]")
        if reg_project:
            console.print(f"  [dim]  project: {reg_project}[/dim]")

        # Check binary exists
        if not Path(reg_binary).exists():
            console.print(f"  [red]✗[/red] Registered binary does not exist: {reg_binary}")
            console.print("  [dim]  Fix: sema init --claude --uninstall && sema init --claude[/dim]")
            ok = False
        else:
            # Cross-check with `claude mcp list` for live status
            if claude:
                result = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True)
                mcp_output = result.stdout + result.stderr
                if "Failed" in mcp_output and "sema" in mcp_output:
                    console.print("  [red]✗[/red] Registered but Claude reports Failed")
                    console.print("  [dim]  Fix: sema init --claude --uninstall && sema init --claude[/dim]")
                    ok = False
                else:
                    console.print("  [green]✔[/green] Registered and binary exists")

            # Check if registered project matches cwd
            if reg_project and Path(reg_project).resolve() != project_root:
                console.print("  [yellow]⚠[/yellow]  Registered project does not match cwd")
                console.print(f"  [dim]     registered: {reg_project}[/dim]")
                console.print(f"  [dim]     cwd:        {project_root}[/dim]")
                console.print("  [dim]     Fix: sema init --claude --uninstall && sema init --claude[/dim]")
                warnings += 1
    else:
        console.print("  [yellow]–[/yellow] sema not registered — run: sema init --claude")
        warnings += 1

    # Check for stale project-level config (old sema versions wrote here)
    old_config = Path(".claude/settings.json")
    if old_config.exists():
        try:
            old_data = json.loads(old_config.read_text())
            if "mcpServers" in old_data and "sema" in old_data["mcpServers"]:
                console.print(f"  [yellow]⚠[/yellow]  Old project-level config found: {old_config}")
                console.print("  [dim]  This can conflict with user-level registration.[/dim]")
                console.print(f"  [dim]  Fix: remove the 'sema' key from {old_config}[/dim]")
                warnings += 1
        except Exception:
            pass

    if not claude:
        console.print("  [dim]  (claude CLI not found — live status check skipped)[/dim]")

    # ── 5/6. TOML-configured clients (Codex, Grok Build) ─────────────────────
    def _doctor_toml_client(heading: str, config_path: Path, label: str, flag: str) -> tuple[bool, int]:
        """Report one TOML-configured client. Returns (ok, warnings) to fold in."""
        client_ok, client_warnings = True, 0
        fix = f"sema init {flag} --uninstall && sema init {flag}"
        console.print(f"\n[bold]{heading}[/bold]")
        console.print(f"  [dim]  config: {config_path.resolve()}[/dim]")
        if not config_path.exists():
            console.print(f"  [dim]–[/dim] No {config_path} — run sema init {flag} if using {label}")
            return client_ok, client_warnings

        content = config_path.read_text()
        if "[mcp_servers.sema]" not in content:
            console.print(f"  [yellow]–[/yellow] {config_path} exists but sema not registered")
            console.print(f"  [dim]  Fix: sema init {flag}[/dim]")
            return client_ok, client_warnings + 1

        cm = _re.search(r'^command\s*=\s*"([^"]+)"', content, _re.MULTILINE)
        pm = _re.search(r'"--project",\s*"([^"]+)"', content)
        reg_binary = cm.group(1) if cm else None
        reg_project = pm.group(1) if pm else None

        if reg_binary:
            console.print(f"  [dim]  binary:  {reg_binary}[/dim]")
        if reg_project:
            console.print(f"  [dim]  project: {reg_project}[/dim]")

        if reg_binary and not Path(reg_binary).exists():
            console.print(f"  [red]✗[/red] Registered binary does not exist: {reg_binary}")
            console.print(f"  [dim]  Fix: {fix}[/dim]")
            client_ok = False
        else:
            console.print("  [green]✔[/green] Registered and binary exists")

        if reg_project and Path(reg_project).resolve() != project_root:
            console.print("  [yellow]⚠[/yellow]  Registered project does not match cwd")
            console.print(f"  [dim]     registered: {reg_project}[/dim]")
            console.print(f"  [dim]     cwd:        {project_root}[/dim]")
            console.print(f"  [dim]     Fix: {fix}[/dim]")
            client_warnings += 1
        return client_ok, client_warnings

    for heading, cfg, label, flag in [
        ("5. Codex registration", Path(".codex/config.toml"), "Codex", "--codex"),
        ("6. Grok Build registration", Path(".grok/config.toml"), "Grok Build", "--grok"),
    ]:
        client_ok, client_warnings = _doctor_toml_client(heading, cfg, label, flag)
        ok = ok and client_ok
        warnings += client_warnings

    # ── 7. Cursor registration (JSON config) ─────────────────────────────────
    console.print("\n[bold]7. Cursor registration[/bold]")
    cursor_cfg = Path(".cursor/mcp.json")
    console.print(f"  [dim]  config: {cursor_cfg.resolve()}[/dim]")
    cursor_fix = "sema init --cursor --uninstall && sema init --cursor"
    cursor_entry = None
    if cursor_cfg.exists():
        try:
            cursor_entry = json.loads(cursor_cfg.read_text()).get("mcpServers", {}).get("sema")
        except (json.JSONDecodeError, OSError):
            cursor_entry = None
    if not cursor_cfg.exists():
        console.print("  [dim]–[/dim] No .cursor/mcp.json — run sema init --cursor if using Cursor")
    elif not cursor_entry:
        console.print("  [yellow]–[/yellow] .cursor/mcp.json exists but sema not registered")
        console.print("  [dim]  Fix: sema init --cursor[/dim]")
        warnings += 1
    else:
        reg_binary = cursor_entry.get("command", "")
        reg_args = cursor_entry.get("args", [])
        reg_project = reg_args[reg_args.index("--project") + 1] if "--project" in reg_args else None
        if reg_binary:
            console.print(f"  [dim]  binary:  {reg_binary}[/dim]")
        if reg_project:
            console.print(f"  [dim]  project: {reg_project}[/dim]")
        if reg_binary and not Path(reg_binary).exists():
            console.print(f"  [red]✗[/red] Registered binary does not exist: {reg_binary}")
            console.print(f"  [dim]  Fix: {cursor_fix}[/dim]")
            ok = False
        else:
            console.print("  [green]✔[/green] Registered and binary exists")
        if reg_project and Path(reg_project).resolve() != project_root:
            console.print("  [yellow]⚠[/yellow]  Registered project does not match cwd")
            console.print(f"  [dim]     registered: {reg_project}[/dim]")
            console.print(f"  [dim]     cwd:        {project_root}[/dim]")
            console.print(f"  [dim]     Fix: {cursor_fix}[/dim]")
            warnings += 1

    # ── 8. Agent guidance ────────────────────────────────────────────────────
    console.print("\n[bold]8. Agent guidance (skills / instruction files)[/bold]")
    claude_md = Path("CLAUDE.md")
    agents_md = Path("AGENTS.md")
    skill_files = [
        Path(".claude/skills/sema-code-navigation/SKILL.md"),
        Path(".agents/skills/sema-code-navigation/SKILL.md"),
    ]
    found_any = False
    found_sema_guidance = False
    for f in [*skill_files, claude_md, agents_md]:
        if f.exists():
            content = f.read_text()
            if "search_code" in content:
                console.print(f"  [green]✔[/green] {f} found and uses sema tools")
                found_sema_guidance = True
            else:
                console.print(f"  [yellow]⚠[/yellow]  {f} found but does not mention sema tools")
            found_any = True
    if not found_sema_guidance:
        if found_any:
            console.print("  [yellow]⚠[/yellow]  Existing guidance does not enable sema")
        else:
            console.print("  [yellow]⚠[/yellow]  No sema skill, CLAUDE.md, or AGENTS.md found")
        console.print("  [dim]  Run sema setup to install provider-specific navigation skills[/dim]")
        warnings += 1

    # ── 9. Lingering processes ───────────────────────────────────────────────
    console.print("\n[bold]9. Running processes[/bold]")
    result = subprocess.run(["pgrep", "-f", "sema serve"], capture_output=True, text=True)
    pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    if pids:
        console.print(f"  [green]✔[/green] sema serve running (pid {', '.join(pids)})")
    else:
        console.print("  [dim]–[/dim] No sema serve process running (started on demand by AI tool)")

    # ── 10. Index ────────────────────────────────────────────────────────────
    console.print("\n[bold]10. Index[/bold]")
    index_path = Path(".") / DEFAULT_INDEX_DIR
    meta_path = Path(".") / DEFAULT_META_FILE
    if index_path.exists():
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            console.print(f"  [green]✔[/green] Index found — {meta.get('file_count', '?')} files, {meta.get('chunk_count', '?')} chunks")
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
            console.print("  [green]✔[/green] Index directory exists")
    else:
        console.print("  [yellow]–[/yellow] No index in current directory")
        console.print("  [dim]  Fix: sema index .[/dim]")
        warnings += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    if ok and warnings == 0:
        console.print("[green]✔ Everything looks good.[/green]")
    elif ok:
        console.print(f"[yellow]⚠  No errors, but {warnings} warning(s) — see above.[/yellow]")
    else:
        console.print("[red]✗ Issues found — see above.[/red]")
