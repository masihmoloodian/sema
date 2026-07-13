import * as vscode from 'vscode';
import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import { SemaClient, SessionUsage, SearchResult } from './semaClient';
import { ChatMessage, PROVIDERS, getProvider } from './providers';
import { ChatProvider, TokenUsage } from './providers/types';
import { redactPieces, redactionSummary } from './redact';
import { SessionStore, StoredSession, createSession, freshUsage, titleFromMessages } from './sessionStore';

const execFileAsync = promisify(execFile);

const PROVIDER_KEY = 'sema.chat.provider';
const MODEL_KEY = 'sema.chat.model';
const MODE_KEY = 'sema.chat.mode';
const INDEX_KEY = 'sema.chat.useIndex';
const REDACT_KEY = 'sema.chat.redact';
const EFFORT_KEY = 'sema.chat.effort';
const CUSTOM_MODELS_KEY = 'sema.chat.customModels';
const RESOLVED_DEFAULT_KEY = 'sema.chat.resolvedDefault';
// Per-workspace pointer to the session to reopen on the next launch (survives restart).
const ACTIVE_SESSION_KEY = 'sema.chat.activeSessionId';

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '\n… (truncated)' : s;
}

const PLAN_NOTE =
  'You are in plan mode. Investigate the problem and produce a concise, step-by-step ' +
  'implementation plan — the files to change, the approach, and the order of the steps. Do ' +
  'NOT edit files or write the full implementation; output the plan only, then stop.';

function buildSystem(context: string, readsWorkspace: boolean, mode: string): string {
  const plan = mode === 'plan';
  // CLI agents (Claude Code / Codex) run under their own system prompt and read the repo
  // themselves, and Ask/Agent is enforced by CLI flags — not by wording. So impose no
  // persona (a plain "hi" gets a plain reply) except a plan directive in Plan mode. Only
  // pass the retrieved context when the index is on.
  if (readsWorkspace) {
    const parts: string[] = [];
    if (plan) {
      parts.push(PLAN_NOTE, '');
    }
    if (context) {
      parts.push(
        "Relevant code from sema's semantic index (a starting point — read more files if needed):",
        '',
        context,
      );
    }
    return parts.join('\n').trim();
  }

  // API providers are bare models. In Agent mode sema hands them workspace tools (see
  // providers/tools.ts) and runs the tool loop, so tell them to act; otherwise give a short
  // role plus the retrieved code (RAG), since they can't read files themselves.
  const agent = mode === 'agent';
  let lines: string[];
  if (agent) {
    lines = [
      "You are a coding agent working directly in the user's workspace through tools. Available " +
        'tools: search_code (semantic search of the codebase — usually the best first step), ' +
        "get_code (fetch a symbol's full source), grep (regex text search), glob (find files by " +
        'pattern), list_directory, read_file, write_file, edit_file (surgical string replacement — ' +
        'prefer it over rewriting whole files), delete_file, and run_command (shell: builds, tests, ' +
        'git, scaffolding). When the request is a task that inspects or changes the project, use ' +
        'tools to actually do it — explore first (search_code / grep / read_file), then change ' +
        '(edit_file / write_file), then verify (run_command) — instead of only describing the ' +
        'steps, and briefly summarize what you changed when done. For greetings, small talk, or ' +
        'questions that do not require the workspace, reply directly and do NOT call any tool.',
    ];
  } else if (plan) {
    lines = [
      'You are a coding assistant in plan mode. You have read-only tools — search_code, get_code, ' +
        'grep, glob, list_directory, read_file — to investigate the codebase before planning; use ' +
        'them to ground your plan in the actual code. For a greeting or a message that is not a ' +
        'task to plan, reply briefly without using any tool. ' +
        PLAN_NOTE,
    ];
  } else {
    lines = ['You are a coding assistant. Answer the user directly and concisely.'];
  }
  if (context) {
    lines.push('', 'Use the retrieved code below (you cannot read files directly):', '', context);
  } else {
    lines.push(
      '',
      "You cannot read the user's files directly. Answer from the conversation; if their code is",
      'needed, suggest turning on the sema index.',
    );
  }
  return lines.join('\n');
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
  private redactHintShown = false;
  /** Durable, per-workspace session storage (undefined only if storage is unavailable). */
  private store?: SessionStore;
  /** The conversation currently on screen — its `messages` array is the live transcript. */
  private session: StoredSession;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly makeClient: () => SemaClient | undefined,
    private readonly repoRoot: string,
    private readonly sessionUsage: SessionUsage,
    private readonly refresh?: () => void,
  ) {
    try {
      this.store = new SessionStore(context.globalStorageUri.fsPath, repoRoot);
    } catch {
      this.store = undefined;
    }
    this.session = this.loadActiveOrNew();
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

  private get mode(): string {
    return this.context.globalState.get<string>(MODE_KEY) ?? 'ask';
  }

  private get useIndex(): boolean {
    return this.context.globalState.get<boolean>(INDEX_KEY) ?? false;
  }

  private get redact(): boolean {
    return this.context.globalState.get<boolean>(REDACT_KEY) ?? false;
  }

  private get effort(): string {
    const provider = getProvider(this.providerId);
    const stored = this.context.globalState.get<string>(EFFORT_KEY);
    return stored && provider.efforts.includes(stored) ? stored : 'default';
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = this.getHtml();
    view.webview.onDidReceiveMessage((msg) => this.onMessage(msg));
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
    this.session = createSession(this.providerId, this.modelId);
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
    this.session = loaded;
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
      this.session = createSession(this.providerId, this.modelId);
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
      messages: this.session.messages.map((m) => ({ role: m.role, content: m.content })),
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
    term.sendText(`"${bin}" ${provider.auth.login.join(' ')}`);
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
      await execFileAsync(this.cliBinFor(provider), provider.auth.logout, {
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
      const { stdout } = await execFileAsync(this.cliBinFor(provider), provider.auth.status, {
        cwd: this.repoRoot || undefined,
      });
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

  /** When the index toggle turns on, ensure an index exists — build it (with progress) if not. */
  private async ensureIndexReady(): Promise<void> {
    const client = this.makeClient();
    if (!client) {
      return;
    }
    let ready = false;
    try {
      ready = (await client.status()).index.exists === true;
    } catch {
      ready = false;
    }
    if (ready) {
      return;
    }

    this.view?.webview.postMessage({
      type: 'notice',
      text: 'No sema index yet — building it now…',
    });
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'sema: building index…' },
      async () => {
        try {
          await client.index();
          this.view?.webview.postMessage({ type: 'notice', text: 'sema index ready.' });
          this.refresh?.();
        } catch (err) {
          // Build failed — turn the toggle back off so the state stays honest.
          await this.context.globalState.update(INDEX_KEY, false);
          await this.sendConfig();
          this.view?.webview.postMessage({
            type: 'error',
            message: `sema index failed: ${(err as Error).message}`,
          });
        }
      },
    );
  }

  // ── message handling ──────────────────────────────────────────────────────
  private async onMessage(msg: { type: string; [k: string]: unknown }): Promise<void> {
    switch (msg.type) {
      case 'ready':
        await this.sendConfig();
        this.restoreToWebview();
        this.sendSessions();
        void this.refreshAuthState();
        break;
      case 'send':
        await this.handleSend(String(msg.text ?? ''));
        break;
      case 'stop':
        this.controller?.abort();
        break;
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
        await this.context.globalState.update(EFFORT_KEY, String(msg.effort));
        break;
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
    this.view.webview.postMessage({
      type: 'config',
      providers: PROVIDERS.map((p) => ({
        id: p.id,
        label: p.label,
        models: [...p.models, ...this.customModels(p.id)],
        efforts: p.efforts,
      })),
      provider: provider.id,
      model: this.modelId,
      mode: this.mode,
      effort: this.effort,
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
    if (!view || !text.trim()) {
      return;
    }
    const provider = getProvider(this.providerId);
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

    this.history.push({ role: 'user', content: text });
    view.webview.postMessage({ type: 'userMessage', text });

    this.controller = new AbortController();

    // API providers can't read files, so retrieve code and inject it (RAG).
    // CLI providers read the repo themselves — send the prompt directly, like Cursor.
    let context = '';
    if (this.useIndex) {
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
      if (this.controller.signal.aborted) {
        view.webview.postMessage({ type: 'assistantEnd' });
        this.controller = undefined;
        return;
      }
    }

    // PII redaction (opt-in): scrub secrets/PII from everything sent to the model.
    let outContext = context;
    let outMessages: ChatMessage[] = this.history;
    if (this.redact) {
      view.webview.postMessage({ type: 'status', text: 'redacting sensitive data…' });
      const semaBin = vscode.workspace.getConfiguration('sema').get<string>('binaryPath') || 'sema';
      const pieces = [context, ...this.history.map((m) => m.content)];
      const res = await redactPieces(pieces, {
        semaBin,
        cwd: this.repoRoot,
        signal: this.controller.signal,
      });
      outContext = res.pieces[0] ?? context;
      outMessages = this.history.map((m, i) => ({ role: m.role, content: res.pieces[i + 1] ?? m.content }));
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
    let cliBin: string | undefined;
    if (provider.id === 'claude-code') {
      cliBin = cfg.get<string>('chat.claudePath');
    } else if (provider.id === 'codex') {
      cliBin = cfg.get<string>('chat.codexPath');
    } else if (provider.id === 'opencode') {
      cliBin = cfg.get<string>('chat.opencodePath');
    }

    // Resume the CLI session only if it belongs to the current provider; otherwise start fresh.
    const resumeId =
      this.session.cliSessionProvider === provider.id ? this.session.cliSessionId : undefined;
    let assistant = '';
    try {
      await provider.stream({
        apiKey,
        cwd: this.repoRoot,
        cliBin,
        agent,
        plan,
        semaBin: cfg.get<string>('binaryPath') || 'sema',
        effort: this.effort,
        model: this.modelId,
        system: buildSystem(outContext, provider.readsWorkspace, this.mode),
        messages: outMessages,
        maxTokens,
        signal: this.controller.signal,
        sessionId: resumeId,
        onSession: (id) => {
          this.session.cliSessionId = id;
          this.session.cliSessionProvider = provider.id;
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
        onUsage: (u) => this.recordUsage(u),
      });
      this.history.push({ role: 'assistant', content: assistant });
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
  #brand svg { width: 18px; height: 18px; color: var(--vscode-textLink-foreground); }
  #brand .name { font-size: 13px; letter-spacing: .3px; }
  #modelinfo { flex: 1; text-align: right; font-size: 11px; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .iconbtn { width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 6px; background: transparent; color: var(--vscode-foreground); cursor: pointer; opacity: .75; flex: none; }
  .iconbtn:hover { background: var(--vscode-list-hoverBackground); opacity: 1; }

  #chatarea { position: relative; flex: 1; overflow: hidden; }
  #messages { position: absolute; inset: 0; overflow-y: auto; padding: 12px; }
  #empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 24px; text-align: center; pointer-events: none; }
  #empty .logo { color: var(--vscode-textLink-foreground); opacity: .9; line-height: 0; }
  #empty .logo svg { width: 46px; height: 46px; }
  #empty .title { font-size: 18px; font-weight: 600; letter-spacing: .4px; }
  #empty .sub { font-size: 12px; color: var(--vscode-descriptionForeground); max-width: 280px; line-height: 1.55; }

  .msg { margin-bottom: 12px; }
  .msg .bubble { padding: 9px 12px; border-radius: 10px; white-space: normal; word-wrap: break-word; line-height: 1.5; }
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
  #status { padding: 2px 12px; min-height: 14px; font-size: 11px; color: var(--vscode-descriptionForeground); }

  #composer { margin: 0 10px 10px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 12px; background: var(--vscode-input-background); padding: 8px; display: flex; flex-direction: column; gap: 8px; }
  #composer:focus-within { border-color: var(--vscode-focusBorder); }
  #input { resize: none; background: transparent; color: var(--vscode-input-foreground); border: none; outline: none; padding: 2px 4px; font-family: inherit; font-size: var(--vscode-font-size); line-height: 1.5; max-height: 160px; overflow-y: auto; }
  #toolbar { display: flex; align-items: center; gap: 6px; }
  #controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; flex: 1; min-width: 0; }
  #controls select { background: transparent; color: var(--vscode-foreground); border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 6px; padding: 3px 6px; font-size: 11.5px; cursor: pointer; max-width: 160px; }
  #controls select:hover { border-color: var(--vscode-focusBorder); }
  #controls button { background: transparent; color: var(--vscode-foreground); border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 6px; padding: 3px 8px; font-size: 11.5px; cursor: pointer; }
  #controls button:hover { border-color: var(--vscode-focusBorder); }
  #controls button.warn { color: var(--vscode-editorWarning-foreground); border-color: var(--vscode-editorWarning-foreground); }
  #controls button.ok { color: var(--vscode-testing-iconPassed, var(--vscode-foreground)); }
  #send { width: 30px; height: 30px; min-width: 30px; padding: 0; border: none; border-radius: 8px; background: var(--vscode-button-background); color: var(--vscode-button-foreground); cursor: pointer; display: flex; align-items: center; justify-content: center; flex: none; }
  #send:hover { background: var(--vscode-button-hoverBackground); }
  .switch { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; user-select: none; font-size: 11.5px; color: var(--vscode-foreground); }
  .switch input { position: absolute; opacity: 0; width: 0; height: 0; }
  .switch .track { position: relative; width: 26px; height: 15px; border-radius: 999px; background: var(--vscode-dropdown-background); border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); transition: background .15s, border-color .15s; flex: none; }
  .switch .track::after { content: ''; position: absolute; top: 1px; left: 1px; width: 11px; height: 11px; border-radius: 50%; background: var(--vscode-descriptionForeground); transition: transform .15s, background .15s; }
  .switch input:checked + .track { background: var(--vscode-button-background); border-color: var(--vscode-button-background); }
  .switch input:checked + .track::after { transform: translateX(11px); background: var(--vscode-button-foreground); }
  .switch input:focus-visible + .track { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }

  /* ── History browser (session list overlay) ── */
  #histpanel { display: none; position: absolute; inset: 0; z-index: 5; flex-direction: column; background: var(--vscode-sideBar-background, var(--vscode-editor-background)); }
  #histpanel.open { display: flex; }
  #histhead { display: flex; align-items: center; gap: 8px; padding: 10px 12px 8px; }
  #histtitle { font-weight: 600; font-size: 13px; }
  #histspacer { flex: 1; }
  #histnew { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; border-radius: 6px; padding: 4px 10px; font-size: 11.5px; cursor: pointer; }
  #histnew:hover { background: var(--vscode-button-hoverBackground); }
  #histsearch { margin: 0 12px 8px; padding: 6px 9px; border-radius: 8px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); background: var(--vscode-input-background); color: var(--vscode-input-foreground); outline: none; font-family: inherit; font-size: 12px; }
  #histsearch:focus { border-color: var(--vscode-focusBorder); }
  #histlist { flex: 1; overflow-y: auto; padding: 0 8px 10px; }
  #histempty { display: none; padding: 24px 16px; text-align: center; font-size: 12px; color: var(--vscode-descriptionForeground); line-height: 1.6; }
  .hist-item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
  .hist-item:hover { background: var(--vscode-list-hoverBackground); }
  .hist-item.active { background: var(--vscode-list-inactiveSelectionBackground); }
  .hist-main { flex: 1; min-width: 0; }
  .hist-title { font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hist-meta { font-size: 10.5px; color: var(--vscode-descriptionForeground); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hist-time { font-size: 10.5px; color: var(--vscode-descriptionForeground); flex: none; }
  .hist-del { flex: none; width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 5px; background: transparent; color: var(--vscode-descriptionForeground); cursor: pointer; opacity: 0; }
  .hist-item:hover .hist-del { opacity: .8; }
  .hist-del:hover { background: var(--vscode-toolbar-hoverBackground, var(--vscode-list-hoverBackground)); color: var(--vscode-editorError-foreground, var(--vscode-foreground)); opacity: 1; }
</style>
</head>
<body>
  <div id="header">
    <span id="brand"><svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M9 5.2 H18.2 L15 9.4 H5.8 Z"></path><path d="M9 14.6 H18.2 L15 18.8 H5.8 Z"></path></svg><span class="name">sema</span></span>
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
      <div class="logo"><svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M9 5.2 H18.2 L15 9.4 H5.8 Z"></path><path d="M9 14.6 H18.2 L15 18.8 H5.8 Z"></path></svg></div>
      <div class="title">sema</div>
      <div class="sub">Chat with your codebase. Pick a provider below, then <b>Ask</b> a question, let it <b>Plan</b>, or switch to <b>Agent</b> to make changes.</div>
    </div>
  </div>
  <div id="status"></div>
  <div id="composer">
    <textarea id="input" rows="1" placeholder="Ask about your codebase…   (Enter to send · Shift+Enter for newline)"></textarea>
    <div id="toolbar">
      <div id="controls">
        <select id="provider" title="Provider"></select>
        <select id="model" title="Model"></select>
        <select id="mode" title="Ask = read-only Q&amp;A · Plan = propose a plan (no edits) · Agent = make changes">
          <option value="ask">Ask</option>
          <option value="plan">Plan</option>
          <option value="agent">Agent</option>
        </select>
        <select id="effort" title="Reasoning effort"></select>
        <label id="indexswitch" class="switch" title="Use sema's semantic index as extra context (like Cursor's codebase context)"><span>index</span><input type="checkbox" id="indexchk"><span class="track"></span></label>
        <label id="redactswitch" class="switch" title="Redact PII &amp; secrets (emails, API keys, names, locations…) before sending to the model"><span>redact</span><input type="checkbox" id="redactchk"><span class="track"></span></label>
        <button id="setkey" title="Set API key">Set key</button>
        <button id="loginbtn" title="Sign in to the selected CLI (Claude Code / Codex)">Log in</button>
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
    var providerSel = document.getElementById('provider');
    var modelSel = document.getElementById('model');
    var modeSel = document.getElementById('mode');
    var effortSel = document.getElementById('effort');
    var setkeyBtn = document.getElementById('setkey');
    var loginBtn = document.getElementById('loginbtn');
    var loggedInState = false;
    var modelInfo = document.getElementById('modelinfo');
    var curDefaults = {}, curProvider = '';
    function modelIdOf(model){ if (!model || model === '__custom__') return ''; if (model === 'default' && curDefaults[curProvider]) return curDefaults[curProvider]; return model; }
    function showSelectedModel(model){ modelInfo.textContent = modelIdOf(model); }
    var indexChk = document.getElementById('indexchk');
    var useIndex = false;
    function applyIndexBtn(){ indexChk.checked = useIndex; }
    var redactChk = document.getElementById('redactchk');
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
    function renderMessage(role, content){
      if (role === 'assistant'){ addBubble('assistant').innerHTML = render(content); }
      else { addBubble(role).textContent = content; }
    }
    var ICON_SEND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V7"></path><path d="M6.5 12.5 L12 7 L17.5 12.5"></path></svg>';
    var ICON_STOP = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="3"></rect></svg>';
    function setStreaming(on){ streaming = on; sendBtn.innerHTML = on ? ICON_STOP : ICON_SEND; sendBtn.title = on ? 'Stop' : 'Send'; }
    function doSend(){
      if (streaming){ vscode.postMessage({type:'stop'}); return; }
      var text = input.value.trim(); if (!text) return;
      input.value = ''; autogrow(); setStreaming(true); vscode.postMessage({type:'send', text:text});
    }

    sendBtn.addEventListener('click', doSend);
    messagesEl.addEventListener('click', function(e){ var a = e.target && e.target.closest ? e.target.closest('a[href]') : null; if (a){ e.preventDefault(); vscode.postMessage({type:'openLink', url:a.getAttribute('href')}); } });
    input.addEventListener('keydown', function(e){ if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); doSend(); } });
    input.addEventListener('input', autogrow);
    providerSel.addEventListener('change', function(){ vscode.postMessage({type:'setProvider', provider:providerSel.value}); });
    modelSel.addEventListener('change', function(){ if (modelSel.value === '__custom__'){ vscode.postMessage({type:'customModel'}); } else { showSelectedModel(modelSel.value); vscode.postMessage({type:'setModel', model:modelSel.value}); } });
    modeSel.addEventListener('change', function(){ vscode.postMessage({type:'setMode', mode:modeSel.value}); });
    effortSel.addEventListener('change', function(){ vscode.postMessage({type:'setEffort', effort:effortSel.value}); });
    indexChk.addEventListener('change', function(){ useIndex = indexChk.checked; vscode.postMessage({type:'setIndex', value:useIndex}); });
    redactChk.addEventListener('change', function(){ vscode.postMessage({type:'setRedact', value:redactChk.checked}); });
    setkeyBtn.addEventListener('click', function(){ vscode.postMessage({type:'setKey'}); });
    loginBtn.addEventListener('click', function(){ vscode.postMessage({type: loggedInState ? 'logout' : 'login'}); });
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
        curProvider = m.provider; curDefaults = m.defaults || {};
        providerSel.innerHTML = '';
        m.providers.forEach(function(p){ var o = document.createElement('option'); o.value = p.id; o.textContent = p.label; if (p.id === m.provider) o.selected = true; providerSel.appendChild(o); });
        var cur = m.providers.filter(function(p){ return p.id === m.provider; })[0];
        modelSel.innerHTML = '';
        if (cur){ cur.models.forEach(function(md){ var o = document.createElement('option'); o.value = md; var label = md; if (md === 'default' && m.defaults && m.defaults[m.provider]){ label = 'default (' + m.defaults[m.provider] + ')'; } o.textContent = label; if (md === m.model) o.selected = true; modelSel.appendChild(o); }); var co = document.createElement('option'); co.value = '__custom__'; co.textContent = '+ custom id…'; modelSel.appendChild(co); }
        if (m.mode) { modeSel.value = m.mode; }
        effortSel.innerHTML = '';
        var efforts = (cur && cur.efforts) ? cur.efforts : ['default'];
        efforts.forEach(function(ef){ var o = document.createElement('option'); o.value = ef; o.textContent = ef === 'default' ? 'effort: default' : ('effort: ' + ef); if (ef === m.effort) o.selected = true; effortSel.appendChild(o); });
        effortSel.style.display = efforts.length > 1 ? '' : 'none';
        if (typeof m.useIndex === 'boolean'){ useIndex = m.useIndex; applyIndexBtn(); }
        if (typeof m.redact === 'boolean'){ redactChk.checked = m.redact; }
        if (m.requiresKey) {
          setkeyBtn.style.display = '';
          setkeyBtn.textContent = m.hasKey ? 'Key set' : 'Set key';
          setkeyBtn.className = m.hasKey ? 'ok' : 'warn';
        } else {
          setkeyBtn.style.display = 'none';
        }
        if (!m.canLogin){ loginBtn.style.display = 'none'; loggedInState = false; } else { loginBtn.style.display = ''; }
        showSelectedModel(m.model);
      } else if (m.type === 'userMessage'){ hideEmpty(); addBubble('user').textContent = m.text; }
      else if (m.type === 'status'){ statusEl.textContent = m.text; }
      else if (m.type === 'context'){ hideEmpty(); var cdt=document.createElement('details'); cdt.className='ctx'; cdt.open=true; var csm=document.createElement('summary'); var cn=m.items.length; csm.textContent='🔎 sema context — '+cn+' result'+(cn===1?'':'s')+' (click to open)'; cdt.appendChild(csm); var cbd=document.createElement('div'); cbd.className='ctx-body'; m.items.forEach(function(it){ var row=document.createElement('div'); row.className='ctx-item'; row.textContent=it.file+':'+it.line+'  '+it.type+' '+it.name; row.title='Open '+it.file+':'+it.line; row.addEventListener('click', function(){ vscode.postMessage({type:'openFile', file:it.file, line:it.line}); }); cbd.appendChild(row); }); cdt.appendChild(cbd); messagesEl.appendChild(cdt); messagesEl.scrollTop=messagesEl.scrollHeight; }
      else if (m.type === 'assistantStart'){ hideEmpty(); if (revealTimer && answerEl && shownLen < answerRaw.length){ answerEl.innerHTML = render(answerRaw); } if (revealTimer){ clearInterval(revealTimer); revealTimer = null; } curEl = addBubble('assistant'); curEl.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>'; answerEl=null; traceEl=null; answerRaw=''; thinkBody=null; started=false; shownLen=0; setStreaming(true); statusEl.textContent = ''; }
      else if (m.type === 'thinking'){ ensureStructure(); if (!thinkBody){ var dt=document.createElement('details'); dt.className='think'; dt.open=true; var sm=document.createElement('summary'); sm.textContent='Thinking'; dt.appendChild(sm); thinkBody=document.createElement('div'); thinkBody.className='think-body'; dt.appendChild(thinkBody); traceEl.appendChild(dt); } thinkBody.textContent += m.text; messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'activity'){ ensureStructure(); var ac=document.createElement('div'); ac.className='act'; ac.textContent = '⚙ ' + m.tool + (m.detail ? '  ' + m.detail : ''); traceEl.appendChild(ac); messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'delta'){ ensureStructure(); answerRaw += m.text; startReveal(); }
      else if (m.type === 'assistantEnd'){ if (curEl && !started){ curEl.textContent = '(no output)'; } statusEl.textContent = ''; setStreaming(false); curEl = null; }
      else if (m.type === 'error'){ hideEmpty(); if (curEl && !started){ var ew = curEl.parentNode; if (ew && ew.parentNode){ ew.parentNode.removeChild(ew); } curEl = null; } addBubble('error').textContent = m.message; setStreaming(false); statusEl.textContent = ''; }
      else if (m.type === 'clear'){ messagesEl.innerHTML = ''; if (revealTimer){ clearInterval(revealTimer); revealTimer = null; } curEl = null; answerEl = null; setStreaming(false); statusEl.textContent = ''; if (emptyEl){ emptyEl.style.display = ''; } showSelectedModel(modelSel.value); }
      else if (m.type === 'sessions'){ sessionsCache = m.sessions || []; activeSessionId = m.activeId || ''; renderSessions(); }
      else if (m.type === 'restore'){
        messagesEl.innerHTML = ''; if (revealTimer){ clearInterval(revealTimer); revealTimer = null; }
        curEl = null; answerEl = null; traceEl = null; answerRaw = ''; thinkBody = null; started = false; shownLen = 0; setStreaming(false); statusEl.textContent = '';
        if (m.messages && m.messages.length){ hideEmpty(); m.messages.forEach(function(x){ renderMessage(x.role, x.content); }); }
        else if (emptyEl){ emptyEl.style.display = ''; }
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      else if (m.type === 'historyClose'){ closeHistory(); }
      else if (m.type === 'model'){ modelInfo.textContent = '→ ' + m.model; }
      else if (m.type === 'notice'){ hideEmpty(); var nt=document.createElement('div'); nt.className='notice'; nt.textContent = m.text; messagesEl.appendChild(nt); messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'auth'){ if (!m.canLogin){ loginBtn.style.display='none'; loggedInState=false; } else { loginBtn.style.display=''; loggedInState=!!m.loggedIn; loginBtn.textContent = m.loggedIn ? '✓ Signed in' : 'Log in'; loginBtn.className = m.loggedIn ? 'ok' : 'warn'; loginBtn.title = m.loggedIn ? 'Signed in — click to sign out' : 'Sign in to the selected CLI (Claude Code / Codex)'; } }
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
