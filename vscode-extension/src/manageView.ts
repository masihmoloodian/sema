import * as path from 'path';
import * as vscode from 'vscode';
import { SemaClient, SessionUsage, StatusResult } from './semaClient';

function fmt(n: number): string {
  return n.toLocaleString('en-US');
}

function row(label: string, description?: string, icon?: string): vscode.TreeItem {
  const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
  if (description !== undefined) {
    item.description = description;
  }
  if (icon) {
    item.iconPath = new vscode.ThemeIcon(icon);
  }
  return item;
}

function action(label: string, command: string, icon: string): vscode.TreeItem {
  const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
  item.iconPath = new vscode.ThemeIcon(icon);
  item.command = { command, title: label };
  return item;
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

/** Sidebar control panel: index status, paths, registration, and one-click CLI actions. */
export class ManageViewProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private lastStatus?: StatusResult;

  constructor(
    private readonly makeClient: () => SemaClient | undefined,
    private readonly workspaceRoot: string,
    private readonly binaryPath: () => string,
    private readonly isWatching: () => boolean,
    private readonly sessionUsage: SessionUsage,
  ) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element?.contextValue === 'sema.actions') {
      return this.actionRows();
    }
    if (element) {
      return [];
    }

    const client = this.makeClient();
    if (!client) {
      return [row('Open a folder to manage sema')];
    }

    let status: StatusResult;
    try {
      status = await client.status();
    } catch (err) {
      this.lastStatus = undefined;
      return [row('Error', (err as Error).message, 'error'), this.actionsGroup()];
    }
    this.lastStatus = status;

    const idx = status.index;
    const items: vscode.TreeItem[] = [];

    const u = this.sessionUsage;
    if (u.turns > 0) {
      const inDesc = u.cached > 0 ? `${fmt(u.input)} in (${fmt(u.cached)} cached)` : `${fmt(u.input)} in`;
      const tokRow = row('Chat tokens', `${inDesc} · ${fmt(u.output)} out`, 'symbol-number');
      tokRow.tooltip =
        `${u.turns} turn${u.turns === 1 ? '' : 's'} in this chat session.` +
        (u.cached > 0
          ? ` ${fmt(u.cached)} input tokens were re-read from cache (cheap). Local CLI agents ` +
            '(Claude Code / Codex) reload their system prompt, built-in tools, your CLAUDE.md, and any ' +
            'registered MCP servers on every message — so input looks large even for a short question.'
          : '');
      items.push(tokRow);

      const costRow = row(
        'Chat cost',
        u.costKnown ? `~$${u.cost.toFixed(4)}` : 'not reported',
        'credit-card',
      );
      costRow.tooltip = u.costKnown
        ? 'Reported by Claude Code, or estimated from token usage for the Anthropic API'
        : 'This provider reports no cost (Codex subscription / OpenAI not estimated)';
      items.push(costRow);
    }

    if (!idx.exists) {
      items.push(row('Index', 'not built — run Re-index', 'circle-slash'));
    } else {
      items.push(row('Index', idx.stale ? 'stale' : 'ready', idx.stale ? 'warning' : 'check'));
      items.push(row('Chunks', String(idx.chunks ?? '?'), 'symbol-numeric'));
      items.push(row('Files', String(idx.files ?? '?'), 'files'));
      items.push(row('Model', String(idx.model ?? '?'), 'symbol-misc'));
      items.push(row('Updated', formatDateTime(idx.indexed_at), 'clock'));
    }

    const projectRoot = idx.project || this.workspaceRoot;
    const indexPath = path.join(projectRoot, '.sema', 'index');
    const idxRow = row('Index path', indexPath, 'folder-opened');
    idxRow.tooltip = indexPath;
    if (idx.exists) {
      idxRow.command = {
        command: 'revealFileInOS',
        title: 'Reveal index folder',
        arguments: [vscode.Uri.file(indexPath)],
      };
    }
    items.push(idxRow);

    const binRow = row('sema binary', this.binaryPath(), 'terminal');
    binRow.tooltip = this.binaryPath();
    items.push(binRow);

    items.push(
      row(
        'Claude Code',
        status.registration.claude ? 'registered' : 'not registered',
        status.registration.claude ? 'check' : 'dash',
      ),
    );
    items.push(
      row(
        'Codex',
        status.registration.codex ? 'registered' : 'not registered',
        status.registration.codex ? 'check' : 'dash',
      ),
    );
    items.push(row('Watch', this.isWatching() ? 'on' : 'off', this.isWatching() ? 'eye' : 'eye-closed'));

    items.push(this.actionsGroup());
    return items;
  }

  private actionsGroup(): vscode.TreeItem {
    const group = new vscode.TreeItem('Actions', vscode.TreeItemCollapsibleState.Expanded);
    group.contextValue = 'sema.actions';
    group.iconPath = new vscode.ThemeIcon('tools');
    return group;
  }

  private actionRows(): vscode.TreeItem[] {
    const reg = this.lastStatus?.registration ?? { claude: false, codex: false };
    return [
      action('Re-index', 'sema.manage.reindex', 'database'),
      action('Re-index (reset)', 'sema.manage.reindexReset', 'debug-restart'),
      reg.claude
        ? action('Unregister Claude Code', 'sema.manage.unregisterClaude', 'circle-slash')
        : action('Register with Claude Code', 'sema.manage.registerClaude', 'plug'),
      reg.codex
        ? action('Unregister Codex', 'sema.manage.unregisterCodex', 'circle-slash')
        : action('Register with Codex', 'sema.manage.registerCodex', 'plug'),
      this.isWatching()
        ? action('Stop watching', 'sema.manage.watchToggle', 'debug-stop')
        : action('Watch files (auto-index)', 'sema.manage.watchToggle', 'eye'),
      action('Run doctor', 'sema.manage.doctor', 'pulse'),
    ];
  }
}
