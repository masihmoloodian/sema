import * as vscode from 'vscode';
import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import { SemaClient, SessionUsage, SearchResult } from './semaClient';
import { ChatMessage, PROVIDERS, getProvider } from './providers';
import {
  AgentPermissionMode,
  Attachment,
  ChatProvider,
  PermissionRequest,
  TokenUsage,
} from './providers/types';
import { redactPieces, redactionSummary } from './redact';
import { SessionStore, StoredSession, createSession, freshUsage, titleFromMessages } from './sessionStore';
import { formatSize, materialize, stage, totalBytes, unstage, LIMITS } from './attachments';
import { buildSystem } from './semaWorkflow';
import { compatibleCliSession, normalizeChatMode, shouldPrefetchIndex } from './chatMode';
import { readPlanArtifact, savePlanArtifact } from './planArtifact';
import {
  EffortCapabilities,
  LEGACY_CODEX_EFFORTS,
  effortsForModel,
  parseClaudeEfforts,
  parseCodexEfforts,
} from './modelSelection';

const execFileAsync = promisify(execFile);

const PROVIDER_KEY = 'sema.chat.provider';
const MODEL_KEY = 'sema.chat.model';
const MODE_KEY = 'sema.chat.mode';
const INDEX_KEY = 'sema.chat.useIndex';
const REDACT_KEY = 'sema.chat.redact';
const EFFORT_KEY = 'sema.chat.effort';
const PERMISSION_KEY = 'sema.chat.permissions';
const CUSTOM_MODELS_KEY = 'sema.chat.customModels';
const RESOLVED_DEFAULT_KEY = 'sema.chat.resolvedDefault';
// Per-workspace pointer to the session to reopen on the next launch (survives restart).
const ACTIVE_SESSION_KEY = 'sema.chat.activeSessionId';

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '\n… (truncated)' : s;
}

/** Turn a provider/SDK error into a user-facing message; explain common rate limits (429). */
function describeError(err: unknown): string {
  const status = (err as { status?: number }).status;
  const raw = (err as Error).message || 'Request failed.';
  if (status === 429 || /\b429\b/.test(raw)) {
    return (
      'Rate limited (429): the provider throttled this request. Wait a few seconds and try again. ' +
      'Free “:free” OpenRouter models share very tight per-minute/day limits — if it keeps ' +
      'happening, switch to a paid model id (drop the “:free” suffix) or add credits.'
    );
  }
  return raw;
}

export class ChatViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private controller?: AbortController;
  private loginTerm?: vscode.Terminal;
  /** Capabilities are keyed by provider + configured executable, so path changes re-probe. */
  private readonly effortCapabilities = new Map<string, EffortCapabilities>();
  private readonly effortDiscoveries = new Map<string, Promise<EffortCapabilities>>();
  private redactHintShown = false;
  /** Keep parallel tool requests from opening overlapping inline permission cards. */
  private permissionQueue: Promise<void> = Promise.resolve();
  private permissionEpoch = 0;
  private nextPermissionId = 0;
  private readonly pendingPermissions = new Map<
    string,
    (decision: 'allow' | 'deny') => void
  >();
  /** Durable, per-workspace session storage (undefined only if storage is unavailable). */
  private store?: SessionStore;
  /** The conversation currently on screen — its `messages` array is the live transcript. */
  private session: StoredSession;
  /** Files staged for the next turn — the composer's chips, not yet part of a message. */
  private pending: Attachment[] = [];

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly makeClient: () => SemaClient | undefined,
    private readonly repoRoot: string,
    private readonly sessionUsage: SessionUsage,
    private readonly refresh?: () => void,
  ) {
    try {
      this.store = new SessionStore(context.globalStorageUri.fsPath, repoRoot);
      // Sweep attachment dirs whose session is gone — an abandoned "New chat", or a
      // turn that errored out and was popped.
      this.store.gcOrphans();
    } catch {
      this.store = undefined;
    }
    this.session = this.loadActiveOrNew();
  }

  /** Where this session's attached files are staged; undefined if storage is unavailable. */
  private get attachmentsDir(): string | undefined {
    return this.store?.attachmentsDir(this.session.id);
  }

  /** The live transcript for the current session. */
  private get history(): ChatMessage[] {
    return this.session.messages;
  }

  /** Reopen the last-active session if it still exists, else start a fresh one. */
  private loadActiveOrNew(): StoredSession {
    const activeId = this.context.workspaceState.get<string>(ACTIVE_SESSION_KEY);
    if (this.store && activeId) {
      const loaded = this.store.load(activeId);
      if (loaded) {
        this.applyUsage(loaded.usage);
        return loaded;
      }
    }
    return createSession(this.providerId, this.modelId);
  }

  private get providerId(): string {
    return this.context.globalState.get<string>(PROVIDER_KEY) ?? PROVIDERS[0].id;
  }

  private customModels(providerId: string): string[] {
    const all = this.context.globalState.get<Record<string, string[]>>(CUSTOM_MODELS_KEY) ?? {};
    return all[providerId] ?? [];
  }

  /** Remember what a provider's 'default' resolved to, so the dropdown can show it. */
  private async rememberDefault(providerId: string, model: string): Promise<void> {
    const all = this.context.globalState.get<Record<string, string>>(RESOLVED_DEFAULT_KEY) ?? {};
    if (all[providerId] === model) {
      return;
    }
    all[providerId] = model;
    await this.context.globalState.update(RESOLVED_DEFAULT_KEY, all);
    await this.sendConfig();
  }

  private get modelId(): string {
    const provider = getProvider(this.providerId);
    const stored = this.context.globalState.get<string>(MODEL_KEY);
    const valid = [...provider.models, ...this.customModels(provider.id)];
    return stored && valid.includes(stored) ? stored : provider.defaultModel;
  }

  private get mode(): 'ask' | 'plan' | 'agent' {
    return normalizeChatMode(this.context.globalState.get<string>(MODE_KEY));
  }

  private get useIndex(): boolean {
    return this.context.globalState.get<boolean>(INDEX_KEY) ?? false;
  }

  private get redact(): boolean {
    return this.context.globalState.get<boolean>(REDACT_KEY) ?? false;
  }

  /**
   * The effort for the current provider, or 'default' when it has none or the stored
   * value isn't one it accepts. The setting is global, so this validation is what keeps
   * a Claude-only 'max' from being sent to Codex (which rejects it) after a switch.
   */
  private get effort(): string {
    const provider = getProvider(this.providerId);
    const stored = this.context.globalState.get<string>(EFFORT_KEY);
    const accepted = this.discoveredEffortsForModel(provider, this.modelId);
    return stored && accepted.includes(stored) ? stored : 'default';
  }

  /** Permission choice is stored per provider so Claude and Codex never overwrite each other. */
  private get permissionMode(): AgentPermissionMode {
    const provider = getProvider(this.providerId);
    const stored =
      this.context.globalState.get<Record<string, AgentPermissionMode>>(PERMISSION_KEY)?.[provider.id];
    return stored && provider.permissionModes?.includes(stored) ? stored : 'ask';
  }

  /** Inline chat consent prompt shared by Claude Agent SDK and Codex app-server. */
  private async requestPermission(request: PermissionRequest): Promise<'allow' | 'deny'> {
    const epoch = this.permissionEpoch;
    let resolveDecision!: (decision: 'allow' | 'deny') => void;
    const decision = new Promise<'allow' | 'deny'>((resolve) => { resolveDecision = resolve; });
    this.permissionQueue = this.permissionQueue
      .then(async () => {
        // A stopped turn may already have queued several parallel tool requests.
        // Do not surface those stale requests after the current one is rejected.
        if (epoch !== this.permissionEpoch) {
          resolveDecision('deny');
          return;
        }

        const id = `permission-${++this.nextPermissionId}`;
        const response = new Promise<'allow' | 'deny'>((resolve) => {
          this.pendingPermissions.set(id, resolve);
        });
        try {
          const posted = await (this.view?.webview.postMessage({
            type: 'permissionRequest',
            id,
            request,
          }) ?? Promise.resolve(false));
          if (!posted) {
            this.resolvePermission(id, 'deny');
          }
          resolveDecision(await response);
        } catch {
          this.resolvePermission(id, 'deny');
          resolveDecision('deny');
        }
      })
      .catch(() => resolveDecision('deny'));
    return decision;
  }

  /** Resolve one visible permission card; unknown or repeated responses are ignored. */
  private resolvePermission(id: string, decision: 'allow' | 'deny'): boolean {
    const resolve = this.pendingPermissions.get(id);
    if (!resolve) {
      return false;
    }
    this.pendingPermissions.delete(id);
    resolve(decision);
    void this.view?.webview.postMessage({ type: 'permissionResolved', id, decision });
    return true;
  }

  /** Reject the visible card and invalidate requests queued by the stopped turn. */
  private cancelPermissions(): void {
    this.permissionEpoch += 1;
    for (const id of [...this.pendingPermissions.keys()]) {
      this.resolvePermission(id, 'deny');
    }
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = this.getHtml();
    // onMessage is async and its result is floating, so an throw here would otherwise be
    // an unhandled rejection — and if it happened mid-turn the spinner would never stop.
    view.webview.onDidReceiveMessage((msg) => {
      void this.onMessage(msg).catch((e: unknown) => {
        this.notifyError(`sema: ${(e as Error).message}`);
        view.webview.postMessage({ type: 'assistantEnd' });
      });
    });
    view.onDidDispose(() => {
      if (this.view === view) {
        this.view = undefined;
        this.cancelPermissions();
      }
    });
    // Re-check sign-in state when the panel regains focus (e.g. after logging in
    // via the terminal) or when the login terminal is closed.
    view.onDidChangeVisibility(() => {
      if (view.visible) {
        void this.refreshAuthState();
      }
    });
    vscode.window.onDidCloseTerminal((t) => {
      if (t === this.loginTerm) {
        this.loginTerm = undefined;
        void this.refreshAuthState();
      }
    });
  }

  /** "New chat" — save the current session (if it has content), then start a fresh one. */
  clearConversation(): void {
    this.persist();
    this.controller?.abort();
    this.cancelPermissions();
    this.session = createSession(this.providerId, this.modelId);
    // Composer chips were staged under the old session's directory, so they can't
    // carry over; gcOrphans sweeps their bytes.
    this.clearPending();
    this.applyUsage(freshUsage());
    void this.context.workspaceState.update(ACTIVE_SESSION_KEY, undefined);
    this.view?.webview.postMessage({ type: 'clear' });
    this.sendSessions();
  }

  /** Overwrite the shared usage tally in place (the Manage view holds the same object). */
  private applyUsage(u: SessionUsage): void {
    this.sessionUsage.input = u.input;
    this.sessionUsage.output = u.output;
    this.sessionUsage.cached = u.cached;
    this.sessionUsage.cost = u.cost;
    this.sessionUsage.costKnown = u.costKnown;
    this.sessionUsage.turns = u.turns;
    this.refresh?.();
  }

  /** Write the current session to disk (best-effort). No-op for an empty conversation. */
  private persist(): void {
    if (!this.store || this.session.messages.length === 0) {
      return;
    }
    this.session.usage = { ...this.sessionUsage };
    this.session.provider = this.providerId;
    this.session.model = this.modelId;
    this.session.updatedAt = Date.now();
    if (!this.session.title || this.session.title === 'New chat') {
      this.session.title = titleFromMessages(this.session.messages);
    }
    try {
      this.store.save(this.session);
      void this.context.workspaceState.update(ACTIVE_SESSION_KEY, this.session.id);
    } catch {
      // Persistence is best-effort — a failed write must never break the chat.
    }
    this.sendSessions();
  }

  /** Reopen a stored session by id, replacing what's on screen. */
  private openSession(id: string): void {
    if (id === this.session.id) {
      this.view?.webview.postMessage({ type: 'historyClose' });
      return;
    }
    this.persist();
    const loaded = this.store?.load(id);
    if (!loaded) {
      this.view?.webview.postMessage({
        type: 'error',
        message: 'sema: could not open that chat — it may have been deleted.',
      });
      this.sendSessions();
      return;
    }
    this.controller?.abort();
    this.cancelPermissions();
    this.session = loaded;
    this.clearPending();
    this.applyUsage(loaded.usage);
    void this.context.workspaceState.update(ACTIVE_SESSION_KEY, loaded.id);
    this.restoreToWebview();
    this.sendSessions();
  }

  /** Delete a stored session; if it's the open one, fall back to a fresh chat. */
  private deleteSession(id: string): void {
    this.store?.delete(id);
    if (id === this.session.id) {
      this.controller?.abort();
      this.cancelPermissions();
      this.session = createSession(this.providerId, this.modelId);
      this.clearPending();
      this.applyUsage(freshUsage());
      void this.context.workspaceState.update(ACTIVE_SESSION_KEY, undefined);
      this.view?.webview.postMessage({ type: 'clear' });
    }
    this.sendSessions();
  }

  /** Push the session list (for the history browser) to the webview. */
  private sendSessions(): void {
    if (!this.view || !this.store) {
      return;
    }
    this.view.webview.postMessage({
      type: 'sessions',
      sessions: this.store.list(),
      activeId: this.session.id,
    });
  }

  /** Re-render the current session's full transcript in the webview. */
  private restoreToWebview(): void {
    this.view?.webview.postMessage({
      type: 'restore',
      messages: this.session.messages.map((m) => ({
        role: m.role,
        content: m.content,
        attachments: m.attachments ?? [],
      })),
    });
  }

  /** Fold one turn's usage into the running session totals and refresh the Manage view. */
  private recordUsage(u: TokenUsage): void {
    this.sessionUsage.input += u.inputTokens;
    this.sessionUsage.output += u.outputTokens;
    this.sessionUsage.cached += u.cachedInputTokens ?? 0;
    this.sessionUsage.turns += 1;
    if (typeof u.costUsd === 'number') {
      this.sessionUsage.cost += u.costUsd;
      this.sessionUsage.costKnown = true;
    }
    this.refresh?.();
  }

  async promptForKey(): Promise<void> {
    const pick = await vscode.window.showQuickPick(
      PROVIDERS.filter((p) => p.requiresKey).map((p) => ({
        label: p.label,
        id: p.id,
        detail: `Key from ${p.keyHint}`,
      })),
      { title: "Set a provider's API key" },
    );
    if (!pick) {
      return;
    }
    const provider = getProvider(pick.id);
    const secretKey = provider.secretKey;
    if (!secretKey) {
      return;
    }
    const key = await vscode.window.showInputBox({
      title: `${provider.label} API key`,
      prompt: `Get one at ${provider.keyHint}. Stored securely in VS Code SecretStorage.`,
      password: true,
      ignoreFocusOut: true,
    });
    if (key === undefined) {
      return;
    }
    if (key === '') {
      await this.context.secrets.delete(secretKey);
    } else {
      await this.context.secrets.store(secretKey, key);
    }
    await this.sendConfig();
    vscode.window.showInformationMessage(`sema: ${provider.label} key ${key ? 'saved' : 'cleared'}.`);
  }

  // ── CLI auth (Claude Code / Codex sign-in) ─────────────────────────────────
  private cliBinFor(provider: ChatProvider): string {
    const cfg = vscode.workspace.getConfiguration('sema');
    if (provider.id === 'claude-code') {
      return cfg.get<string>('chat.claudePath') || 'claude';
    }
    if (provider.id === 'codex') {
      return cfg.get<string>('chat.codexPath') || 'codex';
    }
    if (provider.id === 'opencode') {
      return cfg.get<string>('chat.opencodePath') || 'opencode';
    }
    return provider.id;
  }

  private effortCapabilityKey(provider: ChatProvider): string {
    return `${provider.id}\0${this.cliBinFor(provider)}`;
  }

  private discoveredEffortsForModel(provider: ChatProvider, model: string): readonly string[] {
    const discovered = this.effortCapabilities.get(this.effortCapabilityKey(provider));
    return discovered?.byModel[model] ?? discovered?.efforts ?? effortsForModel(provider, model);
  }

  /** Ask each configured CLI for its real effort contract and cache the result. */
  private async discoverEffortCapabilities(provider: ChatProvider): Promise<EffortCapabilities> {
    if (!provider.efforts?.length) {
      return { efforts: [], byModel: {} };
    }
    const key = this.effortCapabilityKey(provider);
    const cached = this.effortCapabilities.get(key);
    if (cached) {
      return cached;
    }
    const pending = this.effortDiscoveries.get(key);
    if (pending) {
      return pending;
    }
    const discovery = (async (): Promise<EffortCapabilities> => {
      let capabilities: EffortCapabilities | undefined;
      try {
        if (provider.id === 'claude-code') {
          const { stdout } = await execFileAsync(this.cliBinFor(provider), ['--help'], {
            cwd: this.repoRoot || undefined,
            maxBuffer: 2 * 1024 * 1024,
          });
          capabilities = parseClaudeEfforts(stdout);
        } else if (provider.id === 'codex') {
          // `high` is understood by old and new Codex builds. The override lets the
          // catalog load even when config.toml contains a level from a newer CLI.
          const { stdout } = await execFileAsync(
            this.cliBinFor(provider),
            ['-c', 'model_reasoning_effort=high', 'debug', 'models'],
            { cwd: this.repoRoot || undefined, maxBuffer: 10 * 1024 * 1024 },
          );
          capabilities = parseCodexEfforts(stdout);
        }
      } catch {
        // Fall through to the provider-specific compatibility contract below.
      }
      if (!capabilities && provider.id === 'codex') {
        const byModel = Object.fromEntries(
          provider.modelInfos.map((model) => [model.id, LEGACY_CODEX_EFFORTS]),
        );
        capabilities = { efforts: LEGACY_CODEX_EFFORTS, byModel };
      }
      capabilities ??= { efforts: provider.efforts ?? [], byModel: {} };
      this.effortCapabilities.set(key, capabilities);
      return capabilities;
    })();
    this.effortDiscoveries.set(key, discovery);
    try {
      return await discovery;
    } finally {
      this.effortDiscoveries.delete(key);
    }
  }

  /** Codex auth must still open when an older binary cannot parse a newer saved effort. */
  private authArgs(provider: ChatProvider, args: readonly string[]): string[] {
    return provider.id === 'codex'
      ? ['-c', 'model_reasoning_effort=high', ...args]
      : [...args];
  }

  /** Open a terminal and run the CLI's own login — interactive OAuth in the browser. */
  private loginTerminal(): void {
    const provider = getProvider(this.providerId);
    if (!provider.auth) {
      return;
    }
    const bin = this.cliBinFor(provider);
    const term = vscode.window.createTerminal({
      name: `sema · ${provider.label}`,
      cwd: this.repoRoot || undefined,
    });
    this.loginTerm = term;
    term.show();
    term.sendText(`"${bin}" ${this.authArgs(provider, provider.auth.login).join(' ')}`);
    this.view?.webview.postMessage({
      type: 'notice',
      text: `Opened a terminal to sign in to ${provider.label}. Finish in your browser, then send your message again.`,
    });
  }

  private async logout(): Promise<void> {
    const provider = getProvider(this.providerId);
    if (!provider.auth) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(`Sign out of ${provider.label}?`, 'Sign out');
    if (ok !== 'Sign out') {
      return;
    }
    try {
      await execFileAsync(this.cliBinFor(provider), this.authArgs(provider, provider.auth.logout), {
        cwd: this.repoRoot || undefined,
      });
      vscode.window.showInformationMessage(`sema: signed out of ${provider.label}.`);
    } catch (e) {
      vscode.window.showErrorMessage(`sema: sign-out failed: ${(e as Error).message}`);
    }
    await this.refreshAuthState();
  }

  /** Ask the provider CLI whether it's signed in, and update the panel's Login button. */
  private async refreshAuthState(): Promise<void> {
    const view = this.view;
    if (!view) {
      return;
    }
    const provider = getProvider(this.providerId);
    if (!provider.auth) {
      view.webview.postMessage({ type: 'auth', canLogin: false });
      return;
    }
    let loggedIn = false;
    try {
      const { stdout } = await execFileAsync(
        this.cliBinFor(provider),
        this.authArgs(provider, provider.auth.status),
        {
        cwd: this.repoRoot || undefined,
        },
      );
      const out = stdout.trim();
      try {
        // Claude Code prints JSON: { "loggedIn": true, ... }
        const j = JSON.parse(out) as { loggedIn?: boolean };
        loggedIn =
          typeof j.loggedIn === 'boolean'
            ? j.loggedIn
            : !/not\s+logged\s*in|not\s+authenticated|logged\s+out/i.test(out);
      } catch {
        // Codex prints plain text ("Not logged in" when signed out).
        loggedIn = !/not\s+logged\s*in|not\s+authenticated|logged\s+out|no\s+credentials/i.test(out);
      }
    } catch {
      // Non-zero exit (or CLI not found) — treat as not signed in.
      loggedIn = false;
    }
    view.webview.postMessage({ type: 'auth', canLogin: true, loggedIn });
  }

  /** Ensure the index exists and matches current source hashes. */
  private async ensureIndexReady(): Promise<boolean> {
    const client = this.makeClient();
    if (!client) {
      return false;
    }
    let exists = false;
    let stale = false;
    try {
      const status = (await client.status()).index;
      exists = status.exists === true;
      stale = status.stale === true;
    } catch {
      exists = false;
    }
    if (exists && !stale) {
      return true;
    }

    this.view?.webview.postMessage({
      type: 'notice',
      text: stale
        ? 'Source files changed — refreshing the sema index…'
        : 'No sema index yet — building it now…',
    });
    return vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: stale ? 'sema: refreshing index…' : 'sema: building index…',
      },
      async () => {
        try {
          await client.index();
          this.view?.webview.postMessage({
            type: 'notice',
            text: stale ? 'sema index refreshed.' : 'sema index ready.',
          });
          this.refresh?.();
          return true;
        } catch (err) {
          // Build failed — turn the toggle back off so the state stays honest.
          await this.context.globalState.update(INDEX_KEY, false);
          await this.sendConfig();
          this.view?.webview.postMessage({
            type: 'error',
            message: `sema index failed: ${(err as Error).message}`,
          });
          return false;
        }
      },
    );
  }

  // ── attachments ───────────────────────────────────────────────────────────
  /** Push the composer's current chips to the webview. */
  private sendPending(): void {
    this.view?.webview.postMessage({ type: 'attachments', items: this.pending });
  }

  /**
   * Stage one file and add it to the composer. All three ingest routes (picker, drop,
   * paste) land here, so limits, sniffing, and capability checks are enforced once.
   */
  private async addAttachment(name: string, bytes: Uint8Array): Promise<void> {
    const dir = this.attachmentsDir;
    if (!dir) {
      this.notifyError('Attachments need extension storage, which is unavailable.');
      return;
    }
    const staged = await stage(dir, name, bytes);
    if ('error' in staged) {
      this.notifyError(`sema: ${staged.error}`);
      return;
    }
    const att = staged.attachment;
    // Anything we reject below has already been written to disk, so bin it rather than
    // leave bytes behind that only the 24h orphan sweep would ever reach.
    const reject = async (message: string): Promise<void> => {
      await unstage(dir, att.id);
      this.notifyError(message);
    };

    // Refuse up front rather than letting the model silently not see it. Historical
    // attachments degrade instead — see materialize().
    const provider = getProvider(this.providerId);
    if (!provider.accepts(this.modelId).includes(att.kind)) {
      await reject(
        `sema: ${att.name} — ${provider.label} (${this.modelId}) can't read ${att.kind} attachments. ` +
          'Pick a different model, or remove the file.',
      );
      return;
    }
    if (this.redact && att.kind !== 'text') {
      await reject(
        `sema: ${att.name} can't be sent with redact on — redaction only works on text. ` +
          'Turn redact off to send it.',
      );
      return;
    }

    const budget = totalBytes(this.history) + this.pending.reduce((s, a) => s + a.size, 0);
    if (budget + att.size > LIMITS.total) {
      await reject(
        `sema: ${att.name} would push this conversation past the ${formatSize(LIMITS.total)} ` +
          'attachment budget. Start a new chat, or remove some files.',
      );
      return;
    }

    this.pending.push(att);
    this.sendPending();
  }

  /** Read a file from disk (picker / drag from the Explorer) and attach it. */
  private async attachUris(uris: readonly vscode.Uri[]): Promise<void> {
    for (const uri of uris) {
      try {
        const bytes = await vscode.workspace.fs.readFile(uri);
        await this.addAttachment(path.basename(uri.fsPath), bytes);
      } catch (e) {
        this.notifyError(`sema: could not read ${uri.fsPath}: ${(e as Error).message}`);
      }
    }
  }

  /** Open the native picker. Public so the Explorer context-menu command can reuse it. */
  async attachViaDialog(): Promise<void> {
    const picked = await vscode.window.showOpenDialog({
      canSelectMany: true,
      openLabel: 'Attach',
      title: 'Attach files to the sema chat',
    });
    if (picked?.length) {
      await this.attachUris(picked);
    }
  }

  /** Reveal the chat panel and attach the given files (Explorer context menu). */
  async attachFromExplorer(uris: readonly vscode.Uri[]): Promise<void> {
    // Focusing the view resolves the webview if it hasn't been shown yet, so the chips
    // have somewhere to render.
    await vscode.commands.executeCommand('semaChat.focus');
    await this.attachUris(uris);
  }

  private notifyError(message: string): void {
    this.view?.webview.postMessage({ type: 'error', message });
  }

  /** Drop the composer's staged files (their bytes are swept later by gcOrphans). */
  private clearPending(): void {
    this.pending = [];
    this.sendPending();
  }

  // ── message handling ──────────────────────────────────────────────────────
  private async onMessage(msg: { type: string; [k: string]: unknown }): Promise<void> {
    switch (msg.type) {
      case 'ready':
        await this.sendConfig();
        this.restoreToWebview();
        this.sendSessions();
        this.sendPending();
        void this.refreshAuthState();
        break;
      case 'manage':
        await vscode.commands.executeCommand('sema.manage.open');
        break;
      case 'updateAgents':
        await vscode.commands.executeCommand('sema.manage.updateAgents');
        break;
      case 'attach':
        await this.attachViaDialog();
        break;
      case 'attachUris':
        // Dropped from the VS Code Explorer or a file manager — a text/uri-list payload.
        await this.attachUris(
          (msg.uris as string[] | undefined)?.map((u) => vscode.Uri.parse(u)) ?? [],
        );
        break;
      case 'attachData':
        // Pasted from the clipboard — the one route with no path, so bytes come inline.
        await this.addAttachment(
          String(msg.name ?? 'pasted'),
          Buffer.from(String(msg.data ?? ''), 'base64'),
        );
        break;
      case 'removeAttachment':
        this.pending = this.pending.filter((a) => a.id !== String(msg.id));
        this.sendPending();
        break;
      case 'send':
        await this.handleSend(String(msg.text ?? ''));
        break;
      case 'stop':
        this.controller?.abort();
        this.cancelPermissions();
        break;
      case 'permissionDecision': {
        const decision = msg.decision === 'allow' ? 'allow' : 'deny';
        this.resolvePermission(String(msg.id ?? ''), decision);
        break;
      }
      case 'newSession':
        this.clearConversation();
        break;
      case 'listSessions':
        this.sendSessions();
        break;
      case 'openSession':
        this.openSession(String(msg.id ?? ''));
        break;
      case 'deleteSession':
        this.deleteSession(String(msg.id ?? ''));
        break;
      case 'setProvider':
        await this.context.globalState.update(PROVIDER_KEY, String(msg.provider));
        await this.context.globalState.update(MODEL_KEY, undefined);
        await this.sendConfig();
        void this.refreshAuthState();
        break;
      case 'setModel':
        await this.context.globalState.update(MODEL_KEY, String(msg.model));
        // The selected model may support a narrower reasoning range (for example,
        // GPT-5.5 has no max/ultra). Push normalized config back to the webview.
        await this.sendConfig();
        break;
      case 'setMode':
        await this.context.globalState.update(MODE_KEY, String(msg.mode));
        break;
      case 'setIndex':
        await this.context.globalState.update(INDEX_KEY, !!msg.value);
        if (msg.value) {
          await this.ensureIndexReady();
        }
        break;
      case 'setRedact':
        await this.context.globalState.update(REDACT_KEY, !!msg.value);
        break;
      case 'openLink':
        if (msg.url) {
          void vscode.env.openExternal(vscode.Uri.parse(String(msg.url)));
        }
        break;
      case 'setEffort':
        {
          const provider = getProvider(this.providerId);
          await this.discoverEffortCapabilities(provider);
          const requested = String(msg.effort);
          const effort = this.discoveredEffortsForModel(provider, this.modelId).includes(requested)
            ? requested
            : 'default';
          await this.context.globalState.update(EFFORT_KEY, effort);
        }
        break;
      case 'setPermission': {
        const provider = getProvider(this.providerId);
        const requested = String(msg.permission) as AgentPermissionMode;
        if (provider.permissionModes?.includes(requested)) {
          const all =
            this.context.globalState.get<Record<string, AgentPermissionMode>>(PERMISSION_KEY) ?? {};
          all[provider.id] = requested;
          await this.context.globalState.update(PERMISSION_KEY, all);
        }
        await this.sendConfig();
        break;
      }
      case 'customModel': {
        const cmProvider = getProvider(this.providerId);
        const example =
          cmProvider.modelHint ??
          cmProvider.models.find((m) => m !== 'default') ??
          cmProvider.models[0] ??
          'model id';
        const entered = await vscode.window.showInputBox({
          title: `Custom model id — ${cmProvider.label}`,
          prompt: `Enter any ${cmProvider.label} model id — it's added to this provider's model list.`,
          placeHolder: example,
          ignoreFocusOut: true,
        });
        if (entered && entered.trim()) {
          const model = entered.trim();
          const all =
            this.context.globalState.get<Record<string, string[]>>(CUSTOM_MODELS_KEY) ?? {};
          const list = all[this.providerId] ?? [];
          if (!list.includes(model)) {
            list.push(model);
          }
          all[this.providerId] = list;
          await this.context.globalState.update(CUSTOM_MODELS_KEY, all);
          await this.context.globalState.update(MODEL_KEY, model);
        }
        await this.sendConfig();
        break;
      }
      case 'setKey':
        await this.promptForKey();
        break;
      case 'login':
        this.loginTerminal();
        break;
      case 'logout':
        await this.logout();
        break;
      case 'clear':
        this.clearConversation();
        break;
      case 'openFile':
        await this.openFile(String(msg.file ?? ''), Number(msg.line) || 1);
        break;
    }
  }

  private async sendConfig(): Promise<void> {
    if (!this.view) {
      return;
    }
    const provider = getProvider(this.providerId);
    const hasKey =
      provider.requiresKey && provider.secretKey
        ? !!(await this.context.secrets.get(provider.secretKey))
        : false;
    const capabilityEntries = await Promise.all(
      PROVIDERS.map(async (item) =>
        [item.id, await this.discoverEffortCapabilities(item)] as const,
      ),
    );
    const capabilities = new Map(capabilityEntries);
    this.view.webview.postMessage({
      type: 'config',
      providers: PROVIDERS.map((p) => {
        const discovered = capabilities.get(p.id);
        return {
          id: p.id,
          label: p.label,
          models: [...p.models, ...this.customModels(p.id)],
          modelInfos: [
            ...p.modelInfos.map((info) =>
              discovered?.byModel[info.id]
                ? { ...info, efforts: discovered.byModel[info.id] }
                : info,
            ),
            ...this.customModels(p.id).map((id) => ({ id, name: id, section: 'Custom' })),
          ],
          // Empty for providers with no effort control — the picker stays hidden.
          efforts: discovered?.efforts ?? [],
          permissionModes: p.permissionModes ?? [],
        };
      }),
      provider: provider.id,
      model: this.modelId,
      mode: this.mode,
      effort: this.effort,
      permission: this.permissionMode,
      defaults: this.context.globalState.get<Record<string, string>>(RESOLVED_DEFAULT_KEY) ?? {},
      useIndex: this.useIndex,
      redact: this.redact,
      requiresKey: provider.requiresKey,
      hasKey,
      canLogin: !!provider.auth,
    });
  }

  private async handleSend(text: string): Promise<void> {
    const view = this.view;
    // An attachment with no typed text is a valid turn ("what's wrong with this screenshot?").
    if (!view || (!text.trim() && !this.pending.length)) {
      return;
    }
    const provider = getProvider(this.providerId);
    await this.discoverEffortCapabilities(provider);
    let apiKey: string | undefined;
    if (provider.requiresKey) {
      apiKey = provider.secretKey
        ? await this.context.secrets.get(provider.secretKey)
        : undefined;
      if (!apiKey) {
        view.webview.postMessage({
          type: 'error',
          message: `No API key for ${provider.label}. Click “Set key” to add one.`,
        });
        return;
      }
    }

    // Images and PDFs can't be scrubbed, and the UI claims "Redacted before sending"
    // once redaction runs — so refuse rather than ship unscrubbable bytes under that
    // banner. Text attachments are redacted below like any other text.
    const binary = this.pending.filter((a) => a.kind !== 'text');
    if (this.redact && binary.length) {
      view.webview.postMessage({
        type: 'error',
        message:
          `Redaction can't scrub ${binary.map((a) => a.name).join(', ')} — its contents are ` +
          'not text. Turn redact off to send it, or remove the file.',
      });
      return;
    }

    const attachments = this.pending;
    this.history.push({
      role: 'user',
      content: text,
      attachments: attachments.length ? attachments : undefined,
    });
    view.webview.postMessage({ type: 'userMessage', text, attachments });
    this.clearPending();

    this.controller = new AbortController();

    // API providers can't read files, so retrieve code and inject it (RAG).
    // CLI providers read the repo themselves — send the prompt directly, like Cursor.
    let context = '';
    if (shouldPrefetchIndex(this.useIndex, this.mode) && text.trim()) {
      // The visible toggle is authoritative in every mode. Re-check hashes every
      // enabled turn so persisted state cannot serve stale context.
      const indexReady = await this.ensureIndexReady();
      if (indexReady) {
        view.webview.postMessage({ type: 'status', text: 'searching sema index…' });
        const client = this.makeClient();
        if (client) {
          const retrieved = await this.buildContext(text, client);
          context = retrieved.text;
          if (retrieved.items.length) {
            // Show the user exactly what sema pulled into context (clickable to open).
            view.webview.postMessage({
              type: 'context',
              items: retrieved.items.map((r) => ({
                file: r.file,
                name: r.name,
                type: r.type,
                line: r.start_line,
              })),
            });
          }
        }
      }
      if (this.controller.signal.aborted) {
        view.webview.postMessage({ type: 'assistantEnd' });
        this.controller = undefined;
        return;
      }
    }

    // Resolve attachments for the model actually selected: inline text into the prompt,
    // and swap out any image/PDF this model can't read for a placeholder. Historical
    // attachments are the reason this runs over the whole transcript — the composer
    // refuses unsupported *new* files, but a file attached before a provider switch is
    // already in the history and would otherwise be replayed to a model that 400s on it.
    //
    // Redaction narrows the same set rather than adding a second rule: an image
    // attached while redact was off is still in the history, and replaying it on a
    // redacted turn would ship unscrubbable bytes under the "Redacted before sending"
    // banner. Treating redact-on as "this model reads text only" degrades those the
    // same way a provider switch does.
    const accepts = this.redact
      ? provider.accepts(this.modelId).filter((k) => k === 'text')
      : provider.accepts(this.modelId);
    const mat = await materialize(this.history, {
      dir: this.attachmentsDir ?? '',
      accepts,
      reason: this.redact ? "redaction can't scrub it" : undefined,
    });
    for (const w of mat.warnings) {
      view.webview.postMessage({ type: 'notice', text: `⚠ ${w}` });
    }

    // PII redaction (opt-in): scrub secrets/PII from everything sent to the model. Runs
    // after materialize so that text pulled in from attachments is scrubbed too; binary
    // attachments were rejected above, so no base64 can reach the redactor (its EMAIL
    // and card-number patterns would match inside a base64 blob).
    let activePlan =
      this.mode === 'agent' && this.session.planPath
        ? await readPlanArtifact(this.repoRoot, this.session.planPath)
        : '';
    let outContext = context;
    let outMessages: ChatMessage[] = mat.messages;
    if (this.redact) {
      view.webview.postMessage({ type: 'status', text: 'redacting sensitive data…' });
      const semaBin = vscode.workspace.getConfiguration('sema').get<string>('binaryPath') || 'sema';
      const pieces = [context, activePlan, ...mat.messages.map((m) => m.content)];
      const res = await redactPieces(pieces, {
        semaBin,
        cwd: this.repoRoot,
        signal: this.controller.signal,
      });
      outContext = res.pieces[0] ?? context;
      activePlan = res.pieces[1] ?? activePlan;
      // Spread rather than rebuild: `attachments` must survive redaction, or every
      // image would be silently dropped whenever the redact toggle is on.
      outMessages = mat.messages.map((m, i) => ({ ...m, content: res.pieces[i + 2] ?? m.content }));
      const summary = redactionSummary(res.found);
      if (summary) {
        view.webview.postMessage({ type: 'notice', text: `🛡 Redacted before sending: ${summary}.` });
      }
      if (!res.nerRan && !this.redactHintShown) {
        this.redactHintShown = true;
        view.webview.postMessage({
          type: 'notice',
          text: "Redaction is patterns-only. To also catch names/locations, install the model: pip install 'sema-mcp[pii]' then python -m spacy download en_core_web_sm.",
        });
      }
      if (this.controller.signal.aborted) {
        view.webview.postMessage({ type: 'assistantEnd' });
        this.controller = undefined;
        return;
      }
    }

    view.webview.postMessage({ type: 'assistantStart' });
    const cfg = vscode.workspace.getConfiguration('sema');
    const maxTokens = cfg.get<number>('chat.maxTokens', 8192);
    const agent = this.mode === 'agent';
    const plan = this.mode === 'plan';
    const permissionMode = agent && provider.permissionModes ? this.permissionMode : undefined;
    let cliBin: string | undefined;
    if (provider.id === 'claude-code') {
      cliBin = cfg.get<string>('chat.claudePath');
    } else if (provider.id === 'codex') {
      cliBin = cfg.get<string>('chat.codexPath');
    } else if (provider.id === 'opencode') {
      cliBin = cfg.get<string>('chat.opencodePath');
    }

    // A provider/model/mode switch keeps the sema transcript but starts a compatible
    // native CLI run. Codex resume otherwise retains an old model and sandbox.
    const resumeId = compatibleCliSession(
      this.session,
      provider.id,
      this.modelId,
      this.mode,
      permissionMode,
    );
    let assistant = '';
    try {
      await provider.stream({
        apiKey,
        cwd: this.repoRoot,
        cliBin,
        agent,
        plan,
        semaBin: this.useIndex ? cfg.get<string>('binaryPath') || 'sema' : undefined,
        // Only providers that declare an effort argument get one.
        effort: provider.efforts ? this.effort : undefined,
        permissionMode,
        model: this.modelId,
        system: buildSystem(
          outContext,
          provider.readsWorkspace,
          this.mode,
          activePlan,
          this.session.planPath,
          this.useIndex,
        ),
        messages: outMessages,
        attachmentsDir: this.attachmentsDir,
        maxTokens,
        signal: this.controller.signal,
        sessionId: resumeId,
        onSession: (id) => {
          this.session.cliSessionId = id;
          this.session.cliSessionProvider = provider.id;
          this.session.cliSessionModel = this.modelId;
          this.session.cliSessionMode = this.mode;
          this.session.cliSessionPermission = permissionMode;
        },
        onModel: (m) => {
          view.webview.postMessage({ type: 'model', model: m });
          if (this.modelId === 'default') {
            void this.rememberDefault(provider.id, m);
          }
        },
        onDelta: (t) => {
          assistant += t;
          view.webview.postMessage({ type: 'delta', text: t });
        },
        onThinking: (t) => view.webview.postMessage({ type: 'thinking', text: t }),
        onActivity: (tool, detail) => view.webview.postMessage({ type: 'activity', tool, detail }),
        onPermissionRequest: (request) => this.requestPermission(request),
        onUsage: (u) => this.recordUsage(u),
      });
      this.history.push({ role: 'assistant', content: assistant });
      if (plan && assistant.trim() && !this.controller.signal.aborted) {
        try {
          const artifact = await savePlanArtifact(
            this.repoRoot,
            this.session.id,
            titleFromMessages(this.history),
            assistant,
          );
          this.session.planPath = artifact.relativePath;
          view.webview.postMessage({
            type: 'notice',
            text: `Plan saved to ${artifact.relativePath}. Switch to Agent mode to execute it.`,
          });
        } catch (e) {
          view.webview.postMessage({
            type: 'error',
            message: `sema: could not save the plan: ${(e as Error).message}`,
          });
        }
      }
    } catch (err) {
      if (this.controller.signal.aborted) {
        // User stopped — keep the partial reply as a real turn.
        this.history.push({ role: 'assistant', content: assistant });
      } else {
        // Hard failure — drop the user turn, and drop the CLI session so the next turn
        // recovers with fresh, full history.
        this.history.pop();
        this.session.cliSessionId = undefined;
        this.session.cliSessionProvider = undefined;
        this.session.cliSessionModel = undefined;
        this.session.cliSessionMode = undefined;
        this.session.cliSessionPermission = undefined;
        // The turn is gone, so put its text and files back in the composer — otherwise
        // the chips are already cleared and the attachments are unrecoverable.
        this.pending = attachments;
        this.sendPending();
        view.webview.postMessage({ type: 'restoreInput', text });
        const raw = (err as Error).message;
        view.webview.postMessage({ type: 'error', message: describeError(err) });
        // Not signed in? Offer a one-click login for the CLI providers.
        if (
          provider.auth &&
          /not logged in|please run.*\/login|\/login|not authenticated|unauthorized/i.test(raw)
        ) {
          void this.refreshAuthState();
          const pick = await vscode.window.showWarningMessage(
            `sema: not signed in to ${provider.label}.`,
            'Log in',
          );
          if (pick === 'Log in') {
            this.loginTerminal();
          }
        }
      }
    } finally {
      view.webview.postMessage({ type: 'assistantEnd' });
      this.controller = undefined;
      // Save the turn (transcript, usage, CLI-resume handle) so it survives a restart.
      this.persist();
    }
  }

  /** Retrieve relevant code via sema and format it as prompt context. */
  private async buildContext(
    query: string,
    client: SemaClient,
  ): Promise<{ text: string; items: SearchResult[] }> {
    let results: SearchResult[];
    try {
      results = await client.search(query, 8);
    } catch {
      return { text: '', items: [] };
    }
    if (!results.length) {
      return { text: '', items: [] };
    }

    const parts: string[] = ['Relevant code from this repository (retrieved by sema):', ''];
    for (const r of results.slice(0, 3)) {
      let body = '';
      try {
        const impls = await client.get(r.name);
        const match = impls.find((i) => i.file === r.file) ?? impls[0];
        body = match ? match.body : '';
      } catch {
        body = '';
      }
      parts.push(`// ${r.file}:${r.start_line} — ${r.type} ${r.name}`);
      parts.push('```');
      parts.push(body ? truncate(body, 1600) : r.signature);
      parts.push('```', '');
    }

    const rest = results.slice(3);
    if (rest.length) {
      parts.push('Other related symbols:');
      for (const r of rest) {
        parts.push(`- ${r.file}:${r.start_line} — ${r.type} ${r.name}: ${r.signature}`);
      }
    }
    return { text: parts.join('\n'), items: results };
  }

  /** Open a retrieved file in the editor at the given 1-based line. */
  private async openFile(file: string, line: number): Promise<void> {
    if (!file) {
      return;
    }
    try {
      const abs = path.isAbsolute(file) ? file : path.join(this.repoRoot || '', file);
      const doc = await vscode.workspace.openTextDocument(abs);
      const pos = new vscode.Position(Math.max(0, line - 1), 0);
      await vscode.window.showTextDocument(doc, { selection: new vscode.Range(pos, pos) });
    } catch (e) {
      vscode.window.showErrorMessage(`sema: could not open ${file}: ${(e as Error).message}`);
    }
  }

  // ── webview html ──────────────────────────────────────────────────────────
  private getHtml(): string {
    const nonce = getNonce();
    // Attachment chips use inline SVG icons rather than image thumbnails, so no img-src
    // is needed — and a 5MB screenshot never has to cross the IPC boundary as base64
    // just to render a 40px preview.
    const csp =
      "default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-" + nonce + "';";
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0; font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); display: flex; flex-direction: column; height: 100vh; }

  #header { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--vscode-panel-border); }
  #brand { display: flex; align-items: center; gap: 6px; font-weight: 600; }
  #brand svg { width: 18px; height: 18px; color: var(--vscode-foreground); }
  #brand .name { font-size: 13px; letter-spacing: .3px; }
  #modelinfo { flex: 1; text-align: right; font-size: 11px; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .iconbtn { width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 6px; background: transparent; color: var(--vscode-foreground); cursor: pointer; opacity: .75; flex: none; }
  .iconbtn:hover { background: var(--vscode-list-hoverBackground); opacity: 1; }

  #chatarea { position: relative; flex: 1; overflow: hidden; }
  #messages { position: absolute; inset: 0; overflow-y: auto; padding: 12px; }
  #empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 24px; text-align: center; pointer-events: none; }
  #empty .logo { color: var(--vscode-foreground); opacity: .9; line-height: 0; }
  #empty .logo svg { width: 46px; height: 46px; }
  #empty .title { font-size: 18px; font-weight: 600; letter-spacing: .4px; }
  #empty .sub { font-size: 12px; color: var(--vscode-descriptionForeground); max-width: 280px; line-height: 1.55; }

  .msg { margin-bottom: 12px; }
  .msg .bubble { padding: 9px 12px; border-radius: 10px; white-space: normal; word-wrap: break-word; overflow-wrap: anywhere; min-width: 0; max-width: 100%; line-height: 1.5; }
  .msg.user { display: flex; justify-content: flex-end; }
  .msg.user .bubble { background: var(--vscode-textBlockQuote-background); border: 1px solid var(--vscode-panel-border); max-width: 88%; }
  .msg.assistant .bubble { background: transparent; padding-left: 2px; padding-right: 2px; }
  .msg.error .bubble { background: var(--vscode-inputValidation-errorBackground); border: 1px solid var(--vscode-inputValidation-errorBorder); }
  pre { background: var(--vscode-textCodeBlock-background); padding: 8px 10px; border-radius: 8px; overflow-x: auto; border: 1px solid var(--vscode-panel-border); }
  code { font-family: var(--vscode-editor-font-family); font-size: 12px; }
  .tablewrap { overflow-x: auto; max-width: 100%; }
  .bubble table { border-collapse: collapse; margin: 6px 0; font-size: 12px; }
  .bubble th, .bubble td { border: 1px solid var(--vscode-panel-border); padding: 3px 7px; text-align: left; vertical-align: top; }
  .bubble th { background: var(--vscode-editor-inactiveSelectionBackground); font-weight: 600; }
  .bubble h3, .bubble h4, .bubble h5, .bubble h6 { margin: 8px 0 4px; font-size: 13px; }
  .bubble ul, .bubble ol { margin: 4px 0; padding-left: 20px; }
  .bubble li { margin: 1px 0; }
  .bubble a { color: var(--vscode-textLink-foreground); text-decoration: none; }
  .bubble a:hover { text-decoration: underline; }
  .bubble strong { font-weight: 600; }
  .typing span { display: inline-block; width: 6px; height: 6px; margin-right: 3px; border-radius: 50%; background: var(--vscode-descriptionForeground); animation: sema-blink 1.2s infinite both; }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes sema-blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
  .trace { margin-bottom: 6px; }
  .act { font-size: 11px; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); padding: 1px 0; }
  details.ctx { font-size: 11px; color: var(--vscode-descriptionForeground); margin: 0 0 12px; background: var(--vscode-textBlockQuote-background); border: 1px solid var(--vscode-panel-border); border-radius: 8px; padding: 6px 10px; }
  details.ctx summary { cursor: pointer; user-select: none; }
  .ctx-body { padding: 6px 0 2px; }
  .ctx-item { font-family: var(--vscode-editor-font-family); padding: 2px 0; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ctx-item:hover { color: var(--vscode-textLink-foreground); text-decoration: underline; }
  details.think { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 6px; }
  details.think summary { cursor: pointer; user-select: none; }
  .think-body { white-space: pre-wrap; font-style: italic; opacity: .85; padding: 4px 0 4px 10px; }
  .notice { text-align: center; font-size: 11px; color: var(--vscode-descriptionForeground); padding: 4px 0; }
  .permission-card { margin: 7px 0; padding: 10px; border: 1px solid var(--vscode-editorWarning-foreground, var(--vscode-panel-border)); border-radius: 9px; background: var(--vscode-textBlockQuote-background); color: var(--vscode-foreground); font-family: var(--vscode-font-family); }
  .permission-head { display: flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 600; }
  .permission-icon { color: var(--vscode-editorWarning-foreground, #d7a500); font-size: 15px; line-height: 1; }
  .permission-detail { margin-top: 7px; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); font-size: 11px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
  .permission-actions { display: flex; align-items: center; gap: 7px; margin-top: 10px; }
  .permission-actions button { border: 1px solid var(--vscode-button-border, transparent); border-radius: 5px; padding: 4px 12px; font-family: inherit; font-size: 11.5px; cursor: pointer; }
  .permission-actions button:disabled { cursor: default; opacity: .65; }
  .permission-allow { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .permission-allow:hover:not(:disabled) { background: var(--vscode-button-hoverBackground); }
  .permission-reject { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  .permission-reject:hover:not(:disabled) { background: var(--vscode-button-secondaryHoverBackground); }
  .permission-state { margin-left: auto; color: var(--vscode-descriptionForeground); font-size: 11px; }
  .permission-card.allowed { border-color: var(--vscode-testing-iconPassed, var(--vscode-editorInfo-foreground)); }
  .permission-card.denied { border-color: var(--vscode-testing-iconFailed, var(--vscode-editorError-foreground)); }
  #status { padding: 2px 12px; min-height: 14px; font-size: 11px; color: var(--vscode-descriptionForeground); }

  #composer { margin: 0 10px 10px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 12px; background: var(--vscode-input-background); padding: 8px; display: flex; flex-direction: column; gap: 8px; }
  #composer:focus-within { border-color: var(--vscode-focusBorder); }
  #composer.drop { border-color: var(--vscode-focusBorder); border-style: dashed; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chips:empty { display: none; }
  .chip { display: inline-flex; align-items: center; gap: 5px; max-width: 220px; padding: 3px 6px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 6px; background: var(--vscode-textBlockQuote-background); font-size: 11px; }
  .chip svg { width: 12px; height: 12px; flex: none; opacity: .8; }
  .chip .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip .sz { color: var(--vscode-descriptionForeground); flex: none; }
  .chip .x { flex: none; width: 14px; height: 14px; display: inline-flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 3px; background: transparent; color: var(--vscode-descriptionForeground); cursor: pointer; font-size: 13px; line-height: 1; }
  .chip .x:hover { background: var(--vscode-toolbar-hoverBackground, var(--vscode-list-hoverBackground)); color: var(--vscode-foreground); }
  .msg .chips { margin-top: 6px; justify-content: flex-end; }
  #input { resize: none; background: transparent; color: var(--vscode-input-foreground); border: none; outline: none; padding: 2px 4px; font-family: inherit; font-size: var(--vscode-font-size); line-height: 1.5; max-height: 160px; overflow-y: auto; }
  #toolbar { display: flex; align-items: center; gap: 6px; }
  #controls { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }
  #controls .spacer { flex: 1; min-width: 0; }
  /* Round icon buttons (attachments) */
  .roundbtn { position: relative; width: 28px; height: 28px; min-width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 999px; background: transparent; color: var(--vscode-foreground); cursor: pointer; flex: none; }
  .roundbtn:hover { border-color: var(--vscode-focusBorder); background: var(--vscode-toolbar-hoverBackground, var(--vscode-list-hoverBackground)); }
  .roundbtn.open { border-color: var(--vscode-focusBorder); }
  .roundbtn svg { width: 15px; height: 15px; }
  .roundbtn .dot, .pill .dot { position: absolute; top: 1px; right: 1px; width: 7px; height: 7px; border-radius: 50%; background: var(--vscode-editorWarning-foreground, #d7a500); border: 1.5px solid var(--vscode-input-background); display: none; }
  .roundbtn.needs .dot, .pill.needs .dot { display: block; }
  /* Focused controls for sema, mode, provider/model, and permissions. */
  .pill { position: relative; display: inline-flex; align-items: center; gap: 5px; max-width: 170px; padding: 4px 10px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 999px; background: transparent; color: var(--vscode-foreground); font-size: 11.5px; cursor: pointer; flex: none; }
  .pill:hover { border-color: var(--vscode-focusBorder); background: var(--vscode-toolbar-hoverBackground, var(--vscode-list-hoverBackground)); }
  .pill.open { border-color: var(--vscode-focusBorder); }
  .pill .cap { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pill .caret { flex: none; opacity: .6; }
  .sema-pill svg:not(.caret) { width: 13px; height: 13px; flex: none; }
  .access-indicator { display: none; align-items: center; gap: 5px; padding: 4px 7px; border: none; border-radius: 5px; background: transparent; color: var(--vscode-foreground); font-family: inherit; font-size: 11.5px; cursor: pointer; white-space: nowrap; }
  .access-indicator:hover { background: var(--vscode-toolbar-hoverBackground, var(--vscode-list-hoverBackground)); }
  .access-indicator svg { width: 13px; height: 13px; flex: none; }
  .access-indicator.danger { color: var(--vscode-editorWarning-foreground, #e85d3f); }
  #modelpill { min-width: 0; }
  #send { width: 30px; height: 30px; min-width: 30px; padding: 0; border: none; border-radius: 8px; background: var(--vscode-foreground); color: var(--vscode-editor-background); cursor: pointer; display: flex; align-items: center; justify-content: center; flex: none; }
  #send:hover { opacity: .82; }
  /* Popover menus */
  .menu { position: fixed; z-index: 50; min-width: 180px; max-width: 300px; max-height: 340px; overflow-y: auto; padding: 4px; border: 1px solid var(--vscode-menu-border, var(--vscode-input-border, var(--vscode-panel-border))); border-radius: 8px; background: var(--vscode-menu-background, var(--vscode-dropdown-background)); color: var(--vscode-menu-foreground, var(--vscode-foreground)); box-shadow: 0 4px 16px rgba(0, 0, 0, .32); }
  .menu-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 5px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  .menu-item:hover { background: var(--vscode-menu-selectionBackground, var(--vscode-list-hoverBackground)); color: var(--vscode-menu-selectionForeground, inherit); }
  .menu-item .check { width: 13px; flex: none; text-align: center; opacity: 0; font-size: 11px; }
  .menu-item.on .check, .menu-item.sel .check { opacity: 1; }
  .menu-item .mtext { flex: 1; overflow: hidden; text-overflow: ellipsis; }
  .menu-item .mhint { flex: none; font-size: 10.5px; color: var(--vscode-descriptionForeground); }
  .menu-sep { height: 1px; margin: 4px 6px; background: var(--vscode-menu-separatorBackground, var(--vscode-panel-border)); }
  .menu-label { padding: 5px 8px 2px; font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--vscode-descriptionForeground); }

  /* ── History browser (session list overlay) ── */
  #histpanel { display: none; position: absolute; inset: 0; z-index: 5; flex-direction: column; background: var(--vscode-sideBar-background, var(--vscode-editor-background)); }
  #histpanel.open { display: flex; }
  #histhead { display: flex; align-items: center; gap: 8px; padding: 10px 12px 8px; }
  #histtitle { font-weight: 600; font-size: 13px; }
  #histspacer { flex: 1; }
  #histnew { background: var(--vscode-foreground); color: var(--vscode-editor-background); border: none; border-radius: 6px; padding: 4px 10px; font-size: 11.5px; cursor: pointer; }
  #histnew:hover { opacity: .82; }
  #histsearch { margin: 0 12px 8px; padding: 6px 9px; border-radius: 8px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); background: var(--vscode-input-background); color: var(--vscode-input-foreground); outline: none; font-family: inherit; font-size: 12px; }
  #histsearch:focus { border-color: var(--vscode-focusBorder); }
  #histlist { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 0 8px 10px; }
  #histempty { display: none; padding: 24px 16px; text-align: center; font-size: 12px; color: var(--vscode-descriptionForeground); line-height: 1.6; }
  .hist-item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 8px; cursor: pointer; min-width: 0; }
  .hist-item:hover { background: var(--vscode-list-hoverBackground); }
  .hist-item.active { background: var(--vscode-list-inactiveSelectionBackground); }
  .hist-main { flex: 1; min-width: 0; }
  .hist-title { font-size: 12.5px; overflow-wrap: anywhere; overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; }
  .hist-meta { font-size: 10.5px; color: var(--vscode-descriptionForeground); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hist-time { font-size: 10.5px; color: var(--vscode-descriptionForeground); flex: none; }
  .hist-del { flex: none; width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 5px; background: transparent; color: var(--vscode-descriptionForeground); cursor: pointer; opacity: 0; }
  .hist-item:hover .hist-del { opacity: .8; }
  .hist-del:hover { background: var(--vscode-toolbar-hoverBackground, var(--vscode-list-hoverBackground)); color: var(--vscode-editorError-foreground, var(--vscode-foreground)); opacity: 1; }
</style>
</head>
<body>
  <div id="header">
    <span id="brand"><svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="0.4" stroke-linejoin="round"><path d="M8.06 3.83 L22.00 3.83 L17.55 8.38 L16.37 9.46 L14.92 9.92 L2.40 9.92 L2.23 9.63 L2.79 7.95 L4.01 6.00 L5.82 4.52 L8.03 3.86 Z"></path><path d="M10.60 12.43 L21.01 12.43 L21.14 13.25 L20.65 15.23 L19.00 17.93 L17.26 19.31 L14.82 20.14 L2.00 20.17 L8.92 13.15 L10.57 12.46 Z"></path></svg><span class="name">sema</span></span>
    <span id="modelinfo" title="Selected model id (→ marks the model a local CLI actually used)"></span>
    <button id="history" class="iconbtn" title="Chat history"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v5h5"></path><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"></path><path d="M12 7v5l4 2"></path></svg></button>
    <button id="clear" class="iconbtn" title="New chat (current chat is saved to history)"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"></path></svg></button>
  </div>
  <div id="chatarea">
    <div id="histpanel">
      <div id="histhead">
        <span id="histtitle">Chats</span>
        <span id="histspacer"></span>
        <button id="histnew" title="Start a new chat">+ New chat</button>
        <button id="histclose" class="iconbtn" title="Close history"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"></path></svg></button>
      </div>
      <input id="histsearch" type="text" placeholder="Search chats…" />
      <div id="histlist"></div>
      <div id="histempty">No saved chats yet. Your conversations are saved here automatically.</div>
    </div>
    <div id="messages"></div>
    <div id="empty">
      <div class="logo"><svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="0.4" stroke-linejoin="round"><path d="M8.06 3.83 L22.00 3.83 L17.55 8.38 L16.37 9.46 L14.92 9.92 L2.40 9.92 L2.23 9.63 L2.79 7.95 L4.01 6.00 L5.82 4.52 L8.03 3.86 Z"></path><path d="M10.60 12.43 L21.01 12.43 L21.14 13.25 L20.65 15.23 L19.00 17.93 L17.26 19.31 L14.82 20.14 L2.00 20.17 L8.92 13.15 L10.57 12.46 Z"></path></svg></div>
      <div class="title">sema</div>
      <div class="sub">Chat with your codebase. <b>Ask</b> a question, let it <b>Plan</b>, or switch to <b>Agent</b> to make changes. Pick a provider &amp; model from the controls below.</div>
    </div>
  </div>
  <div id="status"></div>
  <div id="composer">
    <div id="chips" class="chips"></div>
    <textarea id="input" rows="1" placeholder="Ask about your codebase…   (Enter to send · Shift+Enter for newline)"></textarea>
    <div id="toolbar">
      <div id="controls">
        <button id="plusbtn" class="roundbtn" title="Attach a file"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"></path></svg></button>
        <button id="semabtn" class="pill sema-pill" title="Sema index, redaction, management and updates"><svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="0.4" stroke-linejoin="round"><path d="M8.06 3.83 L22.00 3.83 L17.55 8.38 L16.37 9.46 L14.92 9.92 L2.40 9.92 L2.23 9.63 L2.79 7.95 L4.01 6.00 L5.82 4.52 L8.03 3.86 Z"></path><path d="M10.60 12.43 L21.01 12.43 L21.14 13.25 L20.65 15.23 L19.00 17.93 L17.26 19.31 L14.82 20.14 L2.00 20.17 L8.92 13.15 L10.57 12.46 Z"></path></svg><span class="cap">Sema</span><svg class="caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg></button>
        <button id="modepill" class="pill" title="Ask = read-only Q&amp;A · Plan = propose a plan (no edits) · Agent = make changes"><span class="cap">Ask</span><svg class="caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg></button>
        <span class="spacer"></span>
        <button id="permissionpill" class="access-indicator" title="Require approval — click to change permissions"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v5c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-3z"></path><path d="M9 12l2 2 4-4"></path></svg><span>Approval</span><svg class="caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg></button>
        <button id="modelpill" class="pill" title="Provider, model and reasoning effort"><span class="cap">default</span><svg class="caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg><span class="dot"></span></button>
      </div>
      <button id="send" title="Send"></button>
    </div>
  </div>
  <script nonce="${nonce}">
  (function(){
    var vscode = acquireVsCodeApi();
    var messagesEl = document.getElementById('messages');
    var statusEl = document.getElementById('status');
    var input = document.getElementById('input');
    var sendBtn = document.getElementById('send');
    var plusBtn = document.getElementById('plusbtn');
    var semaBtn = document.getElementById('semabtn');
    var modePill = document.getElementById('modepill');
    var permissionPill = document.getElementById('permissionpill');
    var modelPill = document.getElementById('modelpill');
    var modelInfo = document.getElementById('modelinfo');
    // Single source of truth for every composer control — the pills and popover
    // menus are rebuilt from this on each 'config' / 'auth' message.
    var state = {
      providers: [], provider: '', model: 'default', mode: 'agent', effort: 'default', permission: 'ask',
      defaults: {}, useIndex: false, redact: false,
      requiresKey: false, hasKey: false, canLogin: false, loggedIn: false,
    };
    function providerOf(id){ for (var i=0;i<state.providers.length;i++){ if (state.providers[i].id === id) return state.providers[i]; } return null; }
    function effortsFor(p, model){
      if (!p) return [];
      var infos = p.modelInfos || [];
      for (var i=0;i<infos.length;i++){
        if (infos[i].id === model && infos[i].efforts) return infos[i].efforts;
      }
      return p.efforts || [];
    }
    function modelIdOf(model){ if (!model || model === '__custom__') return ''; if (model === 'default' && state.defaults[state.provider]) return state.defaults[state.provider]; return model; }
    function showSelectedModel(model){ modelInfo.textContent = modelIdOf(model); }
    // Friendly label for the model pill: the model's display name, else its id.
    function modelLabel(){
      var p = providerOf(state.provider);
      if (p){ var infos = p.modelInfos || []; for (var i=0;i<infos.length;i++){ if (infos[i].id === state.model) return infos[i].name || infos[i].id; } }
      return state.model || 'default';
    }
    function modeLabel(){ return state.mode ? state.mode.charAt(0).toUpperCase() + state.mode.slice(1) : 'Ask'; }
    // Reflect current state onto the always-visible pills / buttons.
    function refreshPills(){
      modePill.querySelector('.cap').textContent = modeLabel();
      var activeProvider = providerOf(state.provider);
      var cap = modelPill.querySelector('.cap'); cap.textContent = modelLabel();
      var needs = (state.requiresKey && !state.hasKey) || (state.canLogin && !state.loggedIn);
      var hasPermissionControl = activeProvider && activeProvider.permissionModes && activeProvider.permissionModes.length;
      var fullAccess = state.permission === 'bypass';
      permissionPill.style.display = state.mode === 'agent' && hasPermissionControl ? 'inline-flex' : 'none';
      permissionPill.querySelector('span').textContent = fullAccess ? 'Full access' : 'Approval';
      permissionPill.classList.toggle('danger', fullAccess);
      permissionPill.title = fullAccess
        ? 'Full access is enabled — click to change permissions'
        : 'Require approval is enabled — click to change permissions';
      semaBtn.title = 'Sema index: ' + (state.useIndex ? 'on' : 'off') + ' · Redaction: ' + (state.redact ? 'on' : 'off');
      modelPill.classList.toggle('needs', needs);
      modelPill.title = needs
        ? (state.requiresKey && !state.hasKey ? 'Set an API key to send' : 'Sign in to send')
        : (activeProvider ? activeProvider.label + ' · ' : '') + 'Model: ' + modelIdOf(state.model) + (state.effort !== 'default' ? ' · Effort: ' + state.effort : '');
      showSelectedModel(state.model);
    }
    var composerEl = document.getElementById('composer');
    var chipsEl = document.getElementById('chips');
    var ICON_IMAGE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><path d="M21 15l-5-5L5 21"></path></svg>';
    var ICON_PDF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path></svg>';
    var ICON_TEXT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path><path d="M8 13h8M8 17h5"></path></svg>';
    function iconFor(kind){ return kind === 'image' ? ICON_IMAGE : (kind === 'pdf' ? ICON_PDF : ICON_TEXT); }
    function fmtSize(n){
      if (n < 1024) return n + ' B';
      if (n < 1048576) return Math.round(n / 1024) + ' KB';
      return (n / 1048576).toFixed(1) + ' MB';
    }
    // Build one chip. The filename goes in via textContent — a file called
    // "<img src=x onerror=alert(1)>" must never reach innerHTML.
    function makeChip(a, onRemove){
      var c = document.createElement('span'); c.className = 'chip';
      var ic = document.createElement('span'); ic.innerHTML = iconFor(a.kind); c.appendChild(ic.firstChild);
      var nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = a.name; nm.title = a.name; c.appendChild(nm);
      var sz = document.createElement('span'); sz.className = 'sz'; sz.textContent = fmtSize(a.size); c.appendChild(sz);
      if (onRemove){
        var x = document.createElement('button'); x.className = 'x'; x.textContent = '×'; x.title = 'Remove';
        x.addEventListener('click', function(){ onRemove(a.id); });
        c.appendChild(x);
      }
      return c;
    }
    var pendingCount = 0;
    function renderPending(items){
      chipsEl.innerHTML = '';
      pendingCount = (items || []).length;
      (items || []).forEach(function(a){
        chipsEl.appendChild(makeChip(a, function(id){ vscode.postMessage({type:'removeAttachment', id:id}); }));
      });
    }
    // Chips under a sent turn, read-only.
    function attachRow(items){
      var row = document.createElement('div'); row.className = 'chips';
      items.forEach(function(a){ row.appendChild(makeChip(a, null)); });
      return row;
    }
    // Paste is the one route with no path — the bytes only exist in page context.
    input.addEventListener('paste', function(e){
      var files = (e.clipboardData && e.clipboardData.files) || [];
      if (!files.length) return;
      e.preventDefault();
      for (var i = 0; i < files.length; i++){
        (function(f){
          var r = new FileReader();
          r.onload = function(){
            var s = String(r.result); var comma = s.indexOf(',');
            vscode.postMessage({type:'attachData', name: f.name || 'pasted-image.png', data: s.slice(comma + 1)});
          };
          r.readAsDataURL(f);
        })(files[i]);
      }
    });
    // Drops from the VS Code Explorer or a file manager carry paths; the host reads them.
    composerEl.addEventListener('dragover', function(e){ e.preventDefault(); composerEl.classList.add('drop'); });
    composerEl.addEventListener('dragleave', function(){ composerEl.classList.remove('drop'); });
    composerEl.addEventListener('drop', function(e){
      e.preventDefault(); composerEl.classList.remove('drop');
      var list = e.dataTransfer && e.dataTransfer.getData('text/uri-list');
      if (list){
        var uris = list.split('\\n').map(function(s){ return s.trim(); }).filter(function(s){ return s && s[0] !== '#'; });
        if (uris.length){ vscode.postMessage({type:'attachUris', uris:uris}); return; }
      }
      var files = (e.dataTransfer && e.dataTransfer.files) || [];
      for (var i = 0; i < files.length; i++){
        (function(f){
          var r = new FileReader();
          r.onload = function(){
            var s = String(r.result); var comma = s.indexOf(',');
            vscode.postMessage({type:'attachData', name: f.name, data: s.slice(comma + 1)});
          };
          r.readAsDataURL(f);
        })(files[i]);
      }
    });
    var emptyEl = document.getElementById('empty');
    function hideEmpty(){ if (emptyEl){ emptyEl.style.display = 'none'; } }
    function autogrow(){ input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; }
    var streaming = false, curEl = null, answerEl = null, traceEl = null, answerRaw = '', thinkBody = null, started = false, shownLen = 0, revealTimer = null;
    function ensureStructure(){ if (started) return; started = true; curEl.innerHTML = ''; traceEl = document.createElement('div'); traceEl.className = 'trace'; answerEl = document.createElement('div'); answerEl.className = 'answer'; curEl.appendChild(traceEl); curEl.appendChild(answerEl); }
    // Smoothing buffer: reveal received text at a steady pace that catches up fast (no fake delay).
    function tick(){
      if (!answerEl){ clearInterval(revealTimer); revealTimer = null; return; }
      if (shownLen < answerRaw.length){
        var remaining = answerRaw.length - shownLen;
        shownLen = Math.min(answerRaw.length, shownLen + Math.max(3, Math.ceil(remaining / 5)));
        answerEl.innerHTML = render(answerRaw.slice(0, shownLen));
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else { clearInterval(revealTimer); revealTimer = null; }
    }
    function startReveal(){ if (!revealTimer){ revealTimer = setInterval(tick, 20); } }
    var FENCE = String.fromCharCode(96,96,96), BT = String.fromCharCode(96);

    function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
    function mdInline(s){
      var parts = s.split(BT), out = '';
      for (var i=0;i<parts.length;i++){
        if (i % 2 === 1){ out += '<code>' + parts[i] + '</code>'; continue; }
        var t = parts[i];
        t = t.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
        t = t.replace(/(^|[^*])\\*([^*\\n]+)\\*/g, '$1<em>$2</em>');
        t = t.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^)\\s]+)\\)/g, '<a href="$2">$1</a>');
        out += t;
      }
      return out;
    }
    function mdRow(line){
      return line.trim().replace(/^\\|/, '').replace(/\\|$/, '').split('|').map(function(c){ return c.trim(); });
    }
    function mdTable(lines, start){
      var head = mdRow(lines[start]), i = start + 2, rows = [];
      while (i < lines.length && lines[i].indexOf('|') >= 0 && lines[i].trim() !== ''){ rows.push(mdRow(lines[i])); i++; }
      var h = '<div class="tablewrap"><table><thead><tr>';
      for (var a=0;a<head.length;a++){ h += '<th>' + mdInline(esc(head[a])) + '</th>'; }
      h += '</tr></thead><tbody>';
      for (var r=0;r<rows.length;r++){ h += '<tr>'; for (var c=0;c<rows[r].length;c++){ h += '<td>' + mdInline(esc(rows[r][c])) + '</td>'; } h += '</tr>'; }
      return { html: h + '</tbody></table></div>', next: i };
    }
    function mdBlocks(md){
      var lines = md.split('\\n'), out = '', i = 0;
      while (i < lines.length){
        var line = lines[i];
        if (line.indexOf('|') >= 0 && i+1 < lines.length && lines[i+1].indexOf('|') >= 0 && lines[i+1].indexOf('-') >= 0 && /^[\\s|:\\-]+$/.test(lines[i+1])){
          var tb = mdTable(lines, i); out += tb.html; i = tb.next; continue;
        }
        var hd = /^(#{1,6})\\s+(.*)$/.exec(line);
        if (hd){ var lv = Math.min(6, hd[1].length + 2); out += '<h' + lv + '>' + mdInline(esc(hd[2])) + '</h' + lv + '>'; i++; continue; }
        if (/^\\s*[-*+]\\s+/.test(line)){
          var ul = '';
          while (i < lines.length && /^\\s*[-*+]\\s+/.test(lines[i])){ ul += '<li>' + mdInline(esc(lines[i].replace(/^\\s*[-*+]\\s+/, ''))) + '</li>'; i++; }
          out += '<ul>' + ul + '</ul>'; continue;
        }
        if (/^\\s*\\d+\\.\\s+/.test(line)){
          var ol = '';
          while (i < lines.length && /^\\s*\\d+\\.\\s+/.test(lines[i])){ ol += '<li>' + mdInline(esc(lines[i].replace(/^\\s*\\d+\\.\\s+/, ''))) + '</li>'; i++; }
          out += '<ol>' + ol + '</ol>'; continue;
        }
        if (line.trim() === ''){ out += '<br>'; i++; continue; }
        out += mdInline(esc(line)) + '<br>'; i++;
      }
      return out;
    }
    function render(raw){
      var parts = raw.split(FENCE), html = '';
      for (var i=0;i<parts.length;i++){
        if (i % 2 === 1){
          var code = parts[i].replace(/^[a-zA-Z0-9_+.-]*\\n/, '');
          html += '<pre><code>' + esc(code) + '</code></pre>';
        } else {
          html += mdBlocks(parts[i]);
        }
      }
      return html;
    }
    function addBubble(role){
      var wrap = document.createElement('div'); wrap.className = 'msg ' + role;
      var b = document.createElement('div'); b.className = 'bubble'; wrap.appendChild(b);
      messagesEl.appendChild(wrap); messagesEl.scrollTop = messagesEl.scrollHeight; return b;
    }
    // Re-render a stored message (assistant text goes through the markdown renderer).
    function renderMessage(role, content, attachments){
      if (role === 'assistant'){ addBubble('assistant').innerHTML = render(content); return; }
      var b = addBubble(role);
      b.textContent = content;
      if (attachments && attachments.length){ b.appendChild(attachRow(attachments)); }
    }
    var permissionCards = {};
    function resolvePermissionCard(id, decision){
      var card = permissionCards[id];
      if (!card) return;
      var buttons = card.querySelectorAll('button');
      for (var i=0;i<buttons.length;i++) buttons[i].disabled = true;
      card.classList.add(decision === 'allow' ? 'allowed' : 'denied');
      var result = card.querySelector('.permission-state');
      if (result) result.textContent = decision === 'allow' ? 'Allowed' : 'Rejected';
      delete permissionCards[id];
      statusEl.textContent = '';
    }
    function renderPermissionRequest(m){
      hideEmpty();
      var request = m.request || {};
      var card = document.createElement('section'); card.className = 'permission-card';
      var head = document.createElement('div'); head.className = 'permission-head';
      var icon = document.createElement('span'); icon.className = 'permission-icon'; icon.textContent = '⚠'; head.appendChild(icon);
      var title = document.createElement('span'); title.textContent = request.title || 'Agent permission required'; head.appendChild(title); card.appendChild(head);
      var detailParts = [];
      if (request.tool) detailParts.push('Action: ' + request.tool);
      if (request.detail) detailParts.push(String(request.detail));
      if (detailParts.length){ var detail=document.createElement('div'); detail.className='permission-detail'; detail.textContent=detailParts.join('\\n\\n'); card.appendChild(detail); }
      var actions = document.createElement('div'); actions.className = 'permission-actions';
      var allow = document.createElement('button'); allow.className = 'permission-allow'; allow.textContent = 'Allow';
      var reject = document.createElement('button'); reject.className = 'permission-reject'; reject.textContent = 'Reject';
      var result = document.createElement('span'); result.className = 'permission-state'; result.textContent = 'Waiting for your decision';
      function decide(decision){
        allow.disabled = true; reject.disabled = true; result.textContent = 'Applying decision…';
        vscode.postMessage({type:'permissionDecision', id:String(m.id || ''), decision:decision});
      }
      allow.addEventListener('click', function(){ decide('allow'); });
      reject.addEventListener('click', function(){ decide('deny'); });
      actions.appendChild(allow); actions.appendChild(reject); actions.appendChild(result); card.appendChild(actions);
      permissionCards[String(m.id || '')] = card;
      if (curEl){ ensureStructure(); traceEl.appendChild(card); } else { messagesEl.appendChild(card); }
      statusEl.textContent = 'Waiting for permission…';
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
    var ICON_SEND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V7"></path><path d="M6.5 12.5 L12 7 L17.5 12.5"></path></svg>';
    var ICON_STOP = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="3"></rect></svg>';
    function setStreaming(on){ streaming = on; sendBtn.innerHTML = on ? ICON_STOP : ICON_SEND; sendBtn.title = on ? 'Stop' : 'Send'; }
    function doSend(){
      if (streaming){ vscode.postMessage({type:'stop'}); return; }
      // An attachment on its own is a valid turn, so don't require text.
      var text = input.value.trim(); if (!text && !pendingCount) return;
      input.value = ''; autogrow(); setStreaming(true); vscode.postMessage({type:'send', text:text});
    }

    sendBtn.addEventListener('click', doSend);
    messagesEl.addEventListener('click', function(e){ var a = e.target && e.target.closest ? e.target.closest('a[href]') : null; if (a){ e.preventDefault(); vscode.postMessage({type:'openLink', url:a.getAttribute('href')}); } });
    input.addEventListener('keydown', function(e){ if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); doSend(); } });
    input.addEventListener('input', autogrow);
    // ── popover menus ──────────────────────────────────────────────────────
    // One reusable menu at a time, anchored above its trigger (the composer sits at
    // the bottom of the panel). Items are a flat spec list — see the builders below.
    var openMenu = null, openAnchor = null;
    function closeMenu(){
      if (openMenu){ openMenu.remove(); openMenu = null; }
      if (openAnchor){ openAnchor.classList.remove('open'); openAnchor = null; }
    }
    function onDocMouseDown(e){ if (openMenu && !openMenu.contains(e.target) && (!openAnchor || !openAnchor.contains(e.target))){ closeMenu(); } }
    document.addEventListener('mousedown', onDocMouseDown, true);
    document.addEventListener('keydown', function(e){ if (e.key === 'Escape' && openMenu){ closeMenu(); } });
    window.addEventListener('resize', closeMenu);
    // items: {text, hint, on, sel, check, keepOpen, action} | {sep:true} | {label:'…'}
    function showMenu(anchor, align, items){
      var wasThis = openAnchor === anchor;
      closeMenu();
      if (wasThis) return; // clicking the open trigger again just closes it
      var menu = document.createElement('div'); menu.className = 'menu';
      items.forEach(function(it){
        if (it.sep){ var s = document.createElement('div'); s.className = 'menu-sep'; menu.appendChild(s); return; }
        if (it.label !== undefined){ var l = document.createElement('div'); l.className = 'menu-label'; l.textContent = it.label; menu.appendChild(l); return; }
        var el = document.createElement('div'); el.className = 'menu-item' + (it.on ? ' on' : '') + (it.sel ? ' sel' : '');
        if (it.check || it.sel){ var ck = document.createElement('span'); ck.className = 'check'; ck.textContent = '✓'; el.appendChild(ck); }
        var t = document.createElement('span'); t.className = 'mtext'; t.textContent = it.text; el.appendChild(t);
        if (it.hint){ var h = document.createElement('span'); h.className = 'mhint'; h.textContent = it.hint; el.appendChild(h); }
        el.addEventListener('click', function(ev){
          ev.stopPropagation();
          if (it.keepOpen){ if (it.action) it.action(el); }
          else { closeMenu(); if (it.action) it.action(el); }
        });
        menu.appendChild(el);
      });
      document.body.appendChild(menu);
      var r = anchor.getBoundingClientRect();
      var mw = menu.offsetWidth, mh = menu.offsetHeight, pad = 8;
      var left = align === 'right' ? r.right - mw : r.left;
      left = Math.max(pad, Math.min(left, window.innerWidth - mw - pad));
      var top = r.top - mh - 6;
      if (top < pad) top = r.bottom + 6; // no room above → drop below
      menu.style.left = left + 'px'; menu.style.top = top + 'px';
      openMenu = menu; openAnchor = anchor; anchor.classList.add('open');
    }

    // + is attachments only. Sema owns all index, redaction and maintenance actions.
    plusBtn.addEventListener('click', function(){ vscode.postMessage({type:'attach'}); });
    semaBtn.addEventListener('click', function(){
      showMenu(semaBtn, 'left', [
        { text: 'Use sema index', hint: 'codebase context', check: true, on: state.useIndex, keepOpen: true,
          action: function(el){ state.useIndex = !state.useIndex; el.classList.toggle('on', state.useIndex); refreshPills(); vscode.postMessage({type:'setIndex', value: state.useIndex}); } },
        { text: 'Redact PII & secrets', hint: 'before sending', check: true, on: state.redact, keepOpen: true,
          action: function(el){ state.redact = !state.redact; el.classList.toggle('on', state.redact); refreshPills(); vscode.postMessage({type:'setRedact', value: state.redact}); } },
        { sep: true },
        { text: 'Manage sema…', hint: 'index, setup & doctor', action: function(){ vscode.postMessage({type:'manage'}); } },
        { text: 'Update agent CLIs…', hint: 'Claude, Codex & opencode', action: function(){ vscode.postMessage({type:'updateAgents'}); } },
      ]);
    });

    // Mode pill: Ask / Plan / Agent.
    modePill.addEventListener('click', function(){
      var modes = [
        { id: 'ask', text: 'Ask', hint: 'read-only' },
        { id: 'plan', text: 'Plan', hint: 'propose, no edits' },
        { id: 'agent', text: 'Agent', hint: 'make changes' },
      ];
      showMenu(modePill, 'left', modes.map(function(md){
        return { text: md.text, hint: md.hint, sel: state.mode === md.id,
          action: function(){ state.mode = md.id; refreshPills(); vscode.postMessage({type:'setMode', mode: md.id}); } };
      }));
    });

    // Model control: provider, model, provider-specific effort, and authentication.
    modelPill.addEventListener('click', function(){
      var p = providerOf(state.provider); if (!p) return;
      var infos = p.modelInfos && p.modelInfos.length ? p.modelInfos : (p.models || []).map(function(id){ return {id:id, name:id}; });
      var items = [{ label: 'Provider' }], lastSection = null;
      state.providers.forEach(function(provider){
        items.push({ text: provider.label, sel: provider.id === state.provider,
          action: function(){ state.provider = provider.id; refreshPills(); vscode.postMessage({type:'setProvider', provider: provider.id}); } });
      });
      items.push({ sep: true }, { label: 'Model' });
      infos.forEach(function(mi){
        var sec = mi.section || '';
        if (sec && sec !== lastSection){ items.push({ label: sec }); lastSection = sec; }
        var label = mi.name || mi.id;
        if (mi.id === 'default' && state.defaults[state.provider]){ label = (mi.name || 'Default') + ' (' + state.defaults[state.provider] + ')'; }
        items.push({ text: label, hint: mi.recommended ? '★' : '', sel: mi.id === state.model,
          action: function(){
            state.model = mi.id;
            var validEfforts = effortsFor(p, mi.id);
            if (validEfforts.indexOf(state.effort) < 0){
              state.effort = 'default';
              vscode.postMessage({type:'setEffort', effort:'default'});
            }
            refreshPills();
            vscode.postMessage({type:'setModel', model: mi.id});
          } });
      });
      items.push({ text: '+ Custom model id…', action: function(){ vscode.postMessage({type:'customModel'}); } });
      var efforts = effortsFor(p, state.model);
      if (efforts.length > 1){
        items.push({ sep: true }, { label: 'Reasoning effort' });
        efforts.forEach(function(ef){
          var lbl = ef === 'xhigh' ? 'extra high' : ef;
          items.push({ text: lbl, sel: ef === state.effort,
            action: function(){ state.effort = ef; refreshPills(); vscode.postMessage({type:'setEffort', effort: ef}); } });
        });
      }
      if (state.requiresKey || state.canLogin){ items.push({ sep: true }); }
      if (state.requiresKey){
        items.push({ text: state.hasKey ? 'Change API key' : 'Set API key', hint: state.hasKey ? 'set' : 'required',
          action: function(){ vscode.postMessage({type:'setKey'}); } });
      }
      if (state.canLogin){
        items.push({ text: state.loggedIn ? 'Sign out' : 'Sign in', hint: state.loggedIn ? 'signed in' : 'required',
          action: function(){ vscode.postMessage({type: state.loggedIn ? 'logout' : 'login'}); } });
      }
      showMenu(modelPill, 'right', items);
    });

    // Permissions are intentionally separate from model/provider configuration.
    permissionPill.addEventListener('click', function(){
      showMenu(permissionPill, 'right', [
        { label: 'Permissions' },
        { text: 'Require approval', hint: 'ask before protected actions', sel: state.permission === 'ask',
          action: function(){ state.permission = 'ask'; refreshPills(); vscode.postMessage({type:'setPermission', permission:'ask'}); } },
        { text: 'Bypass permissions', hint: 'danger: unrestricted access', sel: state.permission === 'bypass',
          action: function(){ state.permission = 'bypass'; refreshPills(); vscode.postMessage({type:'setPermission', permission:'bypass'}); } },
      ]);
    });
    document.getElementById('clear').addEventListener('click', function(){ vscode.postMessage({type:'clear'}); });

    // ── history browser (session list overlay) ──
    var histPanel = document.getElementById('histpanel');
    var histList = document.getElementById('histlist');
    var histSearch = document.getElementById('histsearch');
    var histEmpty = document.getElementById('histempty');
    var sessionsCache = [];
    var activeSessionId = '';
    function relTime(ms){
      if (!ms) return '';
      var s = Math.floor((Date.now() - ms) / 1000);
      if (s < 45) return 'now';
      if (s < 90) return '1m';
      var mn = Math.floor(s/60); if (mn < 60) return mn + 'm';
      var h = Math.floor(mn/60); if (h < 24) return h + 'h';
      var d = Math.floor(h/24); if (d < 7) return d + 'd';
      var wk = Math.floor(d/7); if (wk < 5) return wk + 'w';
      var dt = new Date(ms); return (dt.getMonth()+1) + '/' + dt.getDate() + '/' + String(dt.getFullYear()).slice(2);
    }
    function openHistory(){ histPanel.classList.add('open'); histSearch.value=''; vscode.postMessage({type:'listSessions'}); histSearch.focus(); }
    function closeHistory(){ histPanel.classList.remove('open'); }
    var DEL_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6"></path></svg>';
    function renderSessions(){
      var q = (histSearch.value||'').toLowerCase().trim();
      var list = sessionsCache.filter(function(s){ return !q || (s.title||'').toLowerCase().indexOf(q) >= 0; });
      histList.innerHTML = '';
      if (!sessionsCache.length){ histEmpty.style.display='block'; histEmpty.textContent='No saved chats yet. Your conversations are saved here automatically.'; return; }
      if (!list.length){ histEmpty.style.display='block'; histEmpty.textContent='No chats match your search.'; return; }
      histEmpty.style.display='none';
      list.forEach(function(s){
        var row = document.createElement('div'); row.className = 'hist-item' + (s.id === activeSessionId ? ' active' : '');
        var main = document.createElement('div'); main.className='hist-main';
        var t = document.createElement('div'); t.className='hist-title'; t.textContent = s.title || 'New chat'; main.appendChild(t);
        var meta = document.createElement('div'); meta.className='hist-meta'; var bits=[]; if (s.provider) bits.push(s.provider); if (s.messageCount) bits.push(s.messageCount + ' msg' + (s.messageCount===1?'':'s')); meta.textContent = bits.join(' · '); main.appendChild(meta);
        var time = document.createElement('span'); time.className='hist-time'; time.textContent = relTime(s.updatedAt);
        var del = document.createElement('button'); del.className='hist-del'; del.title='Delete chat'; del.innerHTML=DEL_ICON;
        row.appendChild(main); row.appendChild(time); row.appendChild(del);
        del.addEventListener('click', function(e){ e.stopPropagation(); vscode.postMessage({type:'deleteSession', id:s.id}); });
        row.addEventListener('click', function(){ vscode.postMessage({type:'openSession', id:s.id}); closeHistory(); });
        histList.appendChild(row);
      });
    }
    document.getElementById('history').addEventListener('click', openHistory);
    document.getElementById('histclose').addEventListener('click', closeHistory);
    document.getElementById('histnew').addEventListener('click', function(){ vscode.postMessage({type:'newSession'}); closeHistory(); });
    histSearch.addEventListener('input', renderSessions);
    document.addEventListener('keydown', function(e){ if (e.key === 'Escape' && histPanel.classList.contains('open')){ closeHistory(); } });

    window.addEventListener('message', function(ev){
      var m = ev.data;
      if (m.type === 'config'){
        // Fold the config into local state; every pill/menu reads from it.
        state.providers = m.providers || [];
        state.provider = m.provider; state.model = m.model;
        state.defaults = m.defaults || {};
        if (m.mode) state.mode = m.mode;
        if (m.effort) state.effort = m.effort;
        if (m.permission) state.permission = m.permission;
        if (typeof m.useIndex === 'boolean') state.useIndex = m.useIndex;
        if (typeof m.redact === 'boolean') state.redact = m.redact;
        state.requiresKey = !!m.requiresKey;
        state.hasKey = !!m.hasKey;
        state.canLogin = !!m.canLogin;
        if (!m.canLogin) state.loggedIn = false;
        refreshPills();
      } else if (m.type === 'userMessage'){ hideEmpty(); renderMessage('user', m.text, m.attachments); }
      else if (m.type === 'attachments'){ renderPending(m.items); }
      else if (m.type === 'restoreInput'){ input.value = m.text || ''; autogrow(); }
      else if (m.type === 'status'){ statusEl.textContent = m.text; }
      else if (m.type === 'context'){ hideEmpty(); var cdt=document.createElement('details'); cdt.className='ctx'; cdt.open=true; var csm=document.createElement('summary'); var cn=m.items.length; csm.textContent='🔎 sema context — '+cn+' result'+(cn===1?'':'s')+' (click to open)'; cdt.appendChild(csm); var cbd=document.createElement('div'); cbd.className='ctx-body'; m.items.forEach(function(it){ var row=document.createElement('div'); row.className='ctx-item'; row.textContent=it.file+':'+it.line+'  '+it.type+' '+it.name; row.title='Open '+it.file+':'+it.line; row.addEventListener('click', function(){ vscode.postMessage({type:'openFile', file:it.file, line:it.line}); }); cbd.appendChild(row); }); cdt.appendChild(cbd); messagesEl.appendChild(cdt); messagesEl.scrollTop=messagesEl.scrollHeight; }
      else if (m.type === 'assistantStart'){ hideEmpty(); if (revealTimer && answerEl && shownLen < answerRaw.length){ answerEl.innerHTML = render(answerRaw); } if (revealTimer){ clearInterval(revealTimer); revealTimer = null; } curEl = addBubble('assistant'); curEl.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>'; answerEl=null; traceEl=null; answerRaw=''; thinkBody=null; started=false; shownLen=0; setStreaming(true); statusEl.textContent = ''; }
      else if (m.type === 'thinking'){ ensureStructure(); if (!thinkBody){ var dt=document.createElement('details'); dt.className='think'; dt.open=true; var sm=document.createElement('summary'); sm.textContent='Thinking'; dt.appendChild(sm); thinkBody=document.createElement('div'); thinkBody.className='think-body'; dt.appendChild(thinkBody); traceEl.appendChild(dt); } thinkBody.textContent += m.text; messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'activity'){ ensureStructure(); var ac=document.createElement('div'); ac.className='act'; ac.textContent = '⚙ ' + m.tool + (m.detail ? '  ' + m.detail : ''); traceEl.appendChild(ac); messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'permissionRequest'){ renderPermissionRequest(m); }
      else if (m.type === 'permissionResolved'){ resolvePermissionCard(String(m.id || ''), m.decision === 'allow' ? 'allow' : 'deny'); }
      else if (m.type === 'delta'){ ensureStructure(); answerRaw += m.text; startReveal(); }
      else if (m.type === 'assistantEnd'){ if (curEl && !started){ curEl.textContent = '(no output)'; } statusEl.textContent = ''; setStreaming(false); curEl = null; }
      else if (m.type === 'error'){ hideEmpty(); if (curEl && !started){ var ew = curEl.parentNode; if (ew && ew.parentNode){ ew.parentNode.removeChild(ew); } curEl = null; } addBubble('error').textContent = m.message; setStreaming(false); statusEl.textContent = ''; }
      else if (m.type === 'clear'){ messagesEl.innerHTML = ''; permissionCards = {}; if (revealTimer){ clearInterval(revealTimer); revealTimer = null; } curEl = null; answerEl = null; setStreaming(false); statusEl.textContent = ''; if (emptyEl){ emptyEl.style.display = ''; } showSelectedModel(state.model); }
      else if (m.type === 'sessions'){ sessionsCache = m.sessions || []; activeSessionId = m.activeId || ''; renderSessions(); }
      else if (m.type === 'restore'){
        messagesEl.innerHTML = ''; permissionCards = {}; if (revealTimer){ clearInterval(revealTimer); revealTimer = null; }
        curEl = null; answerEl = null; traceEl = null; answerRaw = ''; thinkBody = null; started = false; shownLen = 0; setStreaming(false); statusEl.textContent = '';
        if (m.messages && m.messages.length){ hideEmpty(); m.messages.forEach(function(x){ renderMessage(x.role, x.content, x.attachments); }); }
        else if (emptyEl){ emptyEl.style.display = ''; }
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      else if (m.type === 'historyClose'){ closeHistory(); }
      else if (m.type === 'model'){ modelInfo.textContent = '→ ' + m.model; }
      else if (m.type === 'notice'){ hideEmpty(); var nt=document.createElement('div'); nt.className='notice'; nt.textContent = m.text; messagesEl.appendChild(nt); messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'auth'){ state.canLogin = !!m.canLogin; state.loggedIn = m.canLogin ? !!m.loggedIn : false; refreshPills(); }
    });

    setStreaming(false);
    autogrow();
    vscode.postMessage({type:'ready'});
  })();
  </script>
</body>
</html>`;
  }
}

function getNonce(): string {
  let text = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}
