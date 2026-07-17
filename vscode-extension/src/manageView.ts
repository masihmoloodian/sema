import * as path from 'path';
import * as vscode from 'vscode';
import { SemaClient, SemaNotFoundError, SessionUsage, StatusResult } from './semaClient';

function fmt(n: number): string {
  return n.toLocaleString('en-US');
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return '?';
  }
  const d = new Date(iso);
  if (isNaN(d.getTime())) {
    return iso;
  }
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A QuickPick row; rows with a `run` are actionable, the rest are read-only status. */
type ManageItem = vscode.QuickPickItem & { run?: () => void | Thenable<unknown> };

/**
 * The Manage panel: index status, paths, registration, and one-click CLI actions —
 * surfaced from a single title-bar button as a QuickPick, rather than an always-open
 * sidebar tree. Status is fetched fresh each time the panel opens.
 */
export class ManagePanel {
  constructor(
    private readonly makeClient: () => SemaClient | undefined,
    private readonly workspaceRoot: string,
    private readonly binaryPath: () => string,
    private readonly isWatching: () => boolean,
    private readonly sessionUsage: SessionUsage,
  ) {}

  /** Open the Manage QuickPick — status rows up top, actions below. */
  async open(): Promise<void> {
    const client = this.makeClient();
    if (!client) {
      vscode.window.showErrorMessage('sema: open a folder first.');
      return;
    }

    let status: StatusResult | undefined;
    try {
      status = await client.status();
    } catch (err) {
      if (err instanceof SemaNotFoundError) {
        await this.openNotInstalled();
        return;
      }
      vscode.window.showErrorMessage(`sema: ${(err as Error).message}`);
      // Fall through: still offer the actions so the user can recover (re-index, doctor…).
    }

    const sep = (label: string): ManageItem => ({
      label,
      kind: vscode.QuickPickItemKind.Separator,
    });
    const run = (command: string): (() => Thenable<unknown>) => () =>
      vscode.commands.executeCommand(command);

    const items: ManageItem[] = [];

    // ── Status (read-only rows) ──────────────────────────────────────────────
    if (status) {
      const idx = status.index;
      items.push(sep('Status'));
      if (!idx.exists) {
        items.push({ label: '$(circle-slash) Index', description: 'not built — run Re-index' });
      } else {
        items.push({
          label: `$(${idx.stale ? 'warning' : 'check'}) Index`,
          description: idx.stale ? 'stale' : 'ready',
        });
        items.push({ label: '$(symbol-numeric) Chunks', description: String(idx.chunks ?? '?') });
        items.push({ label: '$(files) Files', description: String(idx.files ?? '?') });
        items.push({ label: '$(symbol-misc) Model', description: String(idx.model ?? '?') });
        items.push({ label: '$(clock) Updated', description: formatDateTime(idx.indexed_at) });
      }
      items.push({
        label: `$(${status.registration.claude ? 'check' : 'dash'}) Claude Code`,
        description: status.registration.claude ? 'registered' : 'not registered',
      });
      items.push({
        label: `$(${status.registration.codex ? 'check' : 'dash'}) Codex`,
        description: status.registration.codex ? 'registered' : 'not registered',
      });
      items.push({
        label: `$(${status.registration.grok ? 'check' : 'dash'}) Grok Build`,
        description: status.registration.grok ? 'registered' : 'not registered',
      });
      items.push({
        label: `$(${this.isWatching() ? 'eye' : 'eye-closed'}) Watch`,
        description: this.isWatching() ? 'on' : 'off',
      });

      const u = this.sessionUsage;
      if (u.turns > 0) {
        const inDesc =
          u.cached > 0 ? `${fmt(u.input)} in (${fmt(u.cached)} cached)` : `${fmt(u.input)} in`;
        items.push({ label: '$(symbol-number) Chat tokens', description: `${inDesc} · ${fmt(u.output)} out` });
        items.push({
          label: '$(credit-card) Chat cost',
          description: u.costKnown ? `~$${u.cost.toFixed(4)}` : 'not reported',
        });
      }
    }

    // ── Actions ──────────────────────────────────────────────────────────────
    const reg = status?.registration ?? { claude: false, codex: false, grok: false };
    items.push(sep('Actions'));
    items.push({ label: '$(database) Re-index', run: run('sema.manage.reindex') });
    items.push({ label: '$(debug-restart) Re-index (reset)', run: run('sema.manage.reindexReset') });
    items.push(
      reg.claude
        ? { label: '$(circle-slash) Unregister Claude Code', run: run('sema.manage.unregisterClaude') }
        : { label: '$(plug) Register with Claude Code', run: run('sema.manage.registerClaude') },
    );
    items.push(
      reg.codex
        ? { label: '$(circle-slash) Unregister Codex', run: run('sema.manage.unregisterCodex') }
        : { label: '$(plug) Register with Codex', run: run('sema.manage.registerCodex') },
    );
    items.push(
      reg.grok
        ? { label: '$(circle-slash) Unregister Grok Build', run: run('sema.manage.unregisterGrok') }
        : { label: '$(plug) Register with Grok Build', run: run('sema.manage.registerGrok') },
    );
    items.push(
      this.isWatching()
        ? { label: '$(debug-stop) Stop watching', run: run('sema.manage.watchToggle') }
        : { label: '$(eye) Watch files (auto-index)', run: run('sema.manage.watchToggle') },
    );
    items.push({ label: '$(pulse) Run doctor', run: run('sema.manage.doctor') });
    items.push({
      label: '$(cloud-download) Update agent CLIs…',
      description: 'Claude Code · Codex · opencode · Grok Build',
      run: run('sema.manage.updateAgents'),
    });

    if (status?.index.exists) {
      const projectRoot = status.index.project || this.workspaceRoot;
      const indexPath = path.join(projectRoot, '.sema', 'index');
      items.push({
        label: '$(folder-opened) Reveal index folder',
        description: indexPath,
        run: () => vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(indexPath)),
      });
    }
    items.push({
      label: '$(terminal) sema binary',
      description: this.binaryPath(),
      run: () => vscode.commands.executeCommand('workbench.action.openSettings', 'sema.binaryPath'),
    });

    await this.pick(items, status ? this.summary(status) : 'sema CLI error — pick an action to recover');
  }

  /** Shown when the sema CLI can't be found — guide the user to install it or point at the binary. */
  private async openNotInstalled(): Promise<void> {
    const items: ManageItem[] = [
      { label: 'sema CLI not found', kind: vscode.QuickPickItemKind.Separator },
      {
        label: '$(cloud-download) Install sema…',
        description: 'copy the install command',
        run: () => vscode.commands.executeCommand('sema.manage.copyInstall'),
      },
      {
        label: '$(link-external) Open install guide',
        run: () => vscode.commands.executeCommand('sema.manage.openInstallDocs'),
      },
      {
        label: '$(gear) Set sema binary path',
        description: this.binaryPath(),
        run: () => vscode.commands.executeCommand('workbench.action.openSettings', 'sema.binaryPath'),
      },
    ];
    await this.pick(items, 'sema CLI not found — install it, then reload the window');
  }

  /** One-line status for the QuickPick placeholder. */
  private summary(s: StatusResult): string {
    const idx = s.index;
    if (!idx.exists) {
      return 'Index not built';
    }
    const bits = [idx.stale ? 'Index stale' : 'Index ready'];
    if (idx.chunks != null) {
      bits.push(`${fmt(idx.chunks)} chunks`);
    }
    if (idx.files != null) {
      bits.push(`${fmt(idx.files)} files`);
    }
    return bits.join(' · ');
  }

  /** Present the rows; run the picked row's action (read-only rows just dismiss). */
  private pick(items: ManageItem[], placeholder: string): Promise<void> {
    return new Promise((resolve) => {
      const qp = vscode.window.createQuickPick<ManageItem>();
      qp.title = 'sema — Manage';
      qp.placeholder = placeholder;
      qp.items = items;
      qp.matchOnDescription = true;
      qp.onDidAccept(() => {
        const sel = qp.selectedItems[0];
        qp.hide();
        void sel?.run?.();
      });
      qp.onDidHide(() => {
        qp.dispose();
        resolve();
      });
      qp.show();
    });
  }
}
