import { spawn, ChildProcess } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { SemaClient, SessionUsage } from './semaClient';
import { ChatViewProvider } from './chatPanel';
import { ManagePanel } from './manageView';
import { StatusBar } from './statusBar';

let watchProc: ChildProcess | undefined;

/** Walk up from `start` to the enclosing git repository root; fall back to `start`. */
function findRepoRoot(start: string): string {
  let dir = start;
  for (let i = 0; i < 50 && dir; i++) {
    if (fs.existsSync(path.join(dir, '.git'))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }
  return start;
}

export function activate(context: vscode.ExtensionContext): void {
  const folder = vscode.workspace.workspaceFolders?.[0];
  const workspaceRoot = folder?.uri.fsPath ?? '';
  // The agent's cwd is the git repo root so it sees the whole project (like Claude Code),
  // even when a subfolder is the open workspace. sema's own index stays on the open folder.
  const repoRoot = workspaceRoot ? findRepoRoot(workspaceRoot) : '';

  const binaryPath = (): string =>
    vscode.workspace.getConfiguration('sema').get<string>('binaryPath', 'sema');

  // One client per workspace keeps sema's local embedding worker warm. Creating a
  // client per command would reload SBERT and add several seconds to every search.
  let semaClientPath = binaryPath();
  let semaClient = workspaceRoot ? new SemaClient(semaClientPath, workspaceRoot) : undefined;
  context.subscriptions.push({ dispose: () => semaClient?.dispose() });
  const makeClient = (): SemaClient | undefined => {
    if (!workspaceRoot) return undefined;
    const currentPath = binaryPath();
    if (!semaClient || currentPath !== semaClientPath) {
      semaClient?.dispose();
      semaClientPath = currentPath;
      semaClient = new SemaClient(currentPath, workspaceRoot);
    }
    return semaClient;
  };

  const requireWorkspace = (): boolean => {
    if (!workspaceRoot) {
      vscode.window.showErrorMessage('sema: open a folder first.');
      return false;
    }
    return true;
  };

  const openResult = async (uri: vscode.Uri, line: number): Promise<void> => {
    const doc = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(doc);
    const pos = new vscode.Position(line, 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  };

  const output = vscode.window.createOutputChannel('sema');
  context.subscriptions.push(output);

  // ── Status bar ─────────────────────────────────────────────────────────────
  const statusBar = new StatusBar(makeClient);
  context.subscriptions.push(statusBar.disposable);
  void statusBar.refresh();
  // External `sema index` runs do not emit a VS Code event. Poll lightly and refresh
  // on focus so the status bar cannot keep showing a stale snapshot after the index
  // is already fresh. A save refreshes the opposite transition (fresh -> stale).
  const statusTimer = setInterval(() => void statusBar.refresh(), 30_000);
  context.subscriptions.push(
    { dispose: () => clearInterval(statusTimer) },
    vscode.window.onDidChangeWindowState((state) => {
      if (state.focused) void statusBar.refresh();
    }),
    vscode.workspace.onDidSaveTextDocument(() => void statusBar.refresh()),
  );

  // Running token/cost totals for the active chat session — shared by the chat
  // (writer, reset on New chat) and the Manage view (reader).
  const sessionUsage: SessionUsage = {
    input: 0,
    output: 0,
    cached: 0,
    cost: 0,
    costKnown: false,
    turns: 0,
  };

  // ── Manage panel (status / paths / registration + one-click actions) ───────
  // Opened on demand from the chat title-bar "Manage" button — a QuickPick, not an
  // always-open sidebar tree. It reads status fresh each time, so there is nothing to
  // keep refreshed here beyond the status bar.
  const managePanel = new ManagePanel(
    makeClient,
    workspaceRoot,
    binaryPath,
    () => !!watchProc,
    sessionUsage,
  );

  const refreshAll = (): void => {
    void statusBar.refresh();
  };

  // ── Chat panel (webview) ───────────────────────────────────────────────────
  // refreshAll keeps the Manage view's session usage current and the status bar
  // honest after a chat-triggered index build.
  const chatProvider = new ChatViewProvider(context, makeClient, repoRoot, sessionUsage, refreshAll);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('semaChat', chatProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  // Run a plain-text CLI action with a progress toast + a full transcript in the output channel.
  const runCli = async (
    title: string,
    fn: (client: SemaClient) => Promise<string | void>,
    opts: { show?: boolean; toast?: (out: string) => string } = {},
  ): Promise<void> => {
    const client = makeClient();
    if (!requireWorkspace() || !client) {
      return;
    }
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title },
      async () => {
        try {
          const out = (await fn(client)) || '';
          if (out.trim()) {
            output.appendLine(out.trim());
            if (opts.show) {
              output.show(true);
            }
          }
          refreshAll();
          if (opts.toast) {
            vscode.window.showInformationMessage(opts.toast(out));
          }
        } catch (err) {
          vscode.window.showErrorMessage(`${title.replace(/…$/, '')} failed: ${(err as Error).message}`);
        }
      },
    );
  };

  const initToast = (out: string): string => {
    const lines = out.split('\n').map((s) => s.trim()).filter(Boolean);
    return `sema: ${lines.find((l) => /[✔✗⚠–]/.test(l)) ?? lines[lines.length - 1] ?? 'done'}`;
  };

  const shellQuote = (value: string): string => `'${value.replace(/'/g, `'"'"'`)}'`;

  context.subscriptions.push(
    vscode.commands.registerCommand('sema.openResult', openResult),

    vscode.commands.registerCommand('sema.search', async () => {
      if (!requireWorkspace()) {
        return;
      }
      const query = await vscode.window.showInputBox({
        prompt: 'Search your codebase semantically',
        placeHolder: 'e.g. where do we validate auth tokens?',
      });
      if (!query) {
        return;
      }
      const limit = vscode.workspace
        .getConfiguration('sema')
        .get<number>('searchResultLimit', 20);
      try {
        const results = await makeClient()!.search(query, limit);
        if (!results.length) {
          vscode.window.showInformationMessage(`sema: no results for "${query}".`);
          return;
        }
        const pick = await vscode.window.showQuickPick(
          results.map((r) => ({
            label: r.name,
            description: `${r.file}:${r.start_line} · ${Math.round(r.score * 100)}%`,
            detail: r.signature,
            result: r,
          })),
          {
            title: `Results for "${query}"`,
            placeHolder: 'Open a result…',
            matchOnDescription: true,
            matchOnDetail: true,
          },
        );
        if (pick) {
          await openResult(
            vscode.Uri.file(path.join(workspaceRoot, pick.result.file)),
            Math.max(0, pick.result.start_line - 1),
          );
        }
      } catch (err) {
        vscode.window.showErrorMessage(`sema search failed: ${(err as Error).message}`);
      }
    }),

    vscode.commands.registerCommand('sema.reuse', async () => {
      if (!requireWorkspace()) {
        return;
      }
      const description = await vscode.window.showInputBox({
        prompt: 'Describe what you are about to build — sema checks if it already exists',
        placeHolder: 'e.g. a function to debounce file-system events',
      });
      if (!description) {
        return;
      }
      try {
        const res = await makeClient()!.reuse(description);
        const labels: Record<string, string> = {
          exists: '$(error) Already exists — reuse or extend',
          related: '$(warning) Related code exists — review first',
          novel: '$(pass) Novel — safe to build',
        };
        const pick = await vscode.window.showQuickPick(
          res.candidates.map((c) => ({
            label: c.name,
            description: `${c.file}:${c.start_line} · ${Math.round(c.score * 100)}%`,
            detail: c.signature,
            result: c,
          })),
          {
            title: `${labels[res.verdict]}  (top ${Math.round(res.top_score * 100)}%)`,
            placeHolder: res.candidates.length ? 'Open a candidate…' : 'No candidates found',
          },
        );
        if (pick) {
          await openResult(
            vscode.Uri.file(path.join(workspaceRoot, pick.result.file)),
            Math.max(0, pick.result.start_line - 1),
          );
        }
      } catch (err) {
        vscode.window.showErrorMessage(`sema reuse failed: ${(err as Error).message}`);
      }
    }),

    vscode.commands.registerCommand('sema.chat.setKey', () => chatProvider.promptForKey()),
    vscode.commands.registerCommand('sema.chat.clear', () => chatProvider.clearConversation()),
    // Explorer context menu passes (clickedUri, selectedUris); the palette passes neither,
    // in which case fall back to the native picker.
    vscode.commands.registerCommand(
      'sema.chat.attach',
      (uri?: vscode.Uri, uris?: vscode.Uri[]) => {
        const picked = uris?.length ? uris : uri ? [uri] : [];
        return picked.length
          ? chatProvider.attachFromExplorer(picked)
          : chatProvider.attachViaDialog();
      },
    ),
    vscode.commands.registerCommand('sema.manage.refresh', () => refreshAll()),
    vscode.commands.registerCommand('sema.manage.open', () => managePanel.open()),

    vscode.commands.registerCommand('sema.manage.updateAgents', async () => {
      if (!requireWorkspace()) return;
      const pick = await vscode.window.showQuickPick(
        [
          {
            label: 'All installed agents',
            provider: undefined,
            detail: 'Claude Code, Codex, opencode, and Grok Build',
          },
          { label: 'Claude Code', provider: 'claude' },
          { label: 'Codex', provider: 'codex' },
          { label: 'opencode', provider: 'opencode' },
          { label: 'Grok Build', provider: 'grok' },
        ],
        {
          title: 'Update coding-agent CLIs',
          placeHolder: 'Runs each agent’s official updater in a terminal',
        },
      );
      if (!pick) return;
      const provider = pick.provider ? ` --provider ${pick.provider}` : '';
      const terminal = vscode.window.createTerminal({ name: 'sema · update agents', cwd: workspaceRoot });
      terminal.show();
      terminal.sendText(`${shellQuote(binaryPath())} update${provider}`);
      vscode.window.showInformationMessage(
        'Agent updater started. Reload VS Code after it finishes to refresh available models.',
      );
    }),

    vscode.commands.registerCommand('sema.manage.copyInstall', async () => {
      const cmd =
        'curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh';
      await vscode.env.clipboard.writeText(cmd);
      const pick = await vscode.window.showInformationMessage(
        'Install command copied. Paste it into a terminal (macOS / Linux), then reload the window.',
        'Open Terminal',
        'Install guide',
      );
      if (pick === 'Open Terminal') {
        vscode.window.createTerminal('sema install').show();
      } else if (pick === 'Install guide') {
        vscode.env.openExternal(vscode.Uri.parse('https://github.com/masihmoloodian/sema#install'));
      }
    }),
    vscode.commands.registerCommand('sema.manage.openInstallDocs', () =>
      vscode.env.openExternal(vscode.Uri.parse('https://github.com/masihmoloodian/sema#install')),
    ),

    vscode.commands.registerCommand('sema.manage.reindex', () =>
      runCli('sema: indexing…', (c) => c.index(), { toast: () => 'sema: index updated.' }),
    ),

    vscode.commands.registerCommand('sema.manage.reindexReset', async () => {
      if (!requireWorkspace()) {
        return;
      }
      const ok = await vscode.window.showWarningMessage(
        'Re-index from scratch? This wipes the existing index and rebuilds it.',
        { modal: true },
        'Re-index',
      );
      if (ok !== 'Re-index') {
        return;
      }
      await runCli('sema: re-indexing (reset)…', (c) => c.index(true), {
        toast: () => 'sema: index rebuilt.',
      });
    }),

    vscode.commands.registerCommand('sema.manage.registerClaude', () =>
      runCli('sema: registering Claude Code…', (c) => c.register('claude', false), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.unregisterClaude', () =>
      runCli('sema: unregistering Claude Code…', (c) => c.register('claude', true), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.registerCodex', () =>
      runCli('sema: registering Codex…', (c) => c.register('codex', false), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.unregisterCodex', () =>
      runCli('sema: unregistering Codex…', (c) => c.register('codex', true), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.registerGrok', () =>
      runCli('sema: registering Grok Build…', (c) => c.register('grok', false), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.unregisterGrok', () =>
      runCli('sema: unregistering Grok Build…', (c) => c.register('grok', true), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.registerCursor', () =>
      runCli('sema: registering Cursor…', (c) => c.register('cursor', false), { toast: initToast }),
    ),
    vscode.commands.registerCommand('sema.manage.unregisterCursor', () =>
      runCli('sema: unregistering Cursor…', (c) => c.register('cursor', true), { toast: initToast }),
    ),

    vscode.commands.registerCommand('sema.manage.doctor', () =>
      runCli('sema: running doctor…', (c) => c.doctor(), { show: true }),
    ),

    vscode.commands.registerCommand('sema.manage.watchToggle', () => {
      if (watchProc) {
        watchProc.kill();
        watchProc = undefined;
        vscode.window.showInformationMessage('sema: stopped watching.');
        refreshAll();
        return;
      }
      if (!requireWorkspace()) {
        return;
      }
      const proc = spawn(binaryPath(), ['watch', '.'], { cwd: workspaceRoot });
      watchProc = proc;
      proc.on('exit', () => {
        if (watchProc === proc) {
          watchProc = undefined;
          refreshAll();
        }
      });
      proc.on('error', (e) => {
        vscode.window.showErrorMessage(`sema watch failed: ${e.message}`);
        if (watchProc === proc) {
          watchProc = undefined;
          refreshAll();
        }
      });
      vscode.window.showInformationMessage('sema: watching for changes — auto-indexing on save.');
      refreshAll();
    }),

    { dispose: () => { watchProc?.kill(); watchProc = undefined; } },
  );
}

export function deactivate(): void {
  watchProc?.kill();
  watchProc = undefined;
}
