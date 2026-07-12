import * as vscode from 'vscode';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { SemaClient, SessionUsage } from './semaClient';
import { ChatMessage, PROVIDERS, getProvider } from './providers';
import { ChatProvider, TokenUsage } from './providers/types';

const execFileAsync = promisify(execFile);

const PROVIDER_KEY = 'sema.chat.provider';
const MODEL_KEY = 'sema.chat.model';
const MODE_KEY = 'sema.chat.mode';
const INDEX_KEY = 'sema.chat.useIndex';
const EFFORT_KEY = 'sema.chat.effort';
const CUSTOM_MODELS_KEY = 'sema.chat.customModels';
const RESOLVED_DEFAULT_KEY = 'sema.chat.resolvedDefault';

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '\n… (truncated)' : s;
}

function buildSystem(context: string, readsWorkspace: boolean): string {
  // CLI agents (Claude Code / Codex) run under their own system prompt and read the repo
  // themselves, and Ask/Agent is enforced by CLI flags — not by wording. So impose no
  // persona: a plain "hi" gets a plain reply, exactly like their terminal apps. Only pass
  // the retrieved context when the index is on.
  if (readsWorkspace) {
    return context
      ? [
          "Relevant code from sema's semantic index (a starting point — read more files if needed):",
          '',
          context,
        ].join('\n')
      : '';
  }

  // API providers are bare models: give them a short role plus the retrieved code (RAG),
  // since they cannot read files themselves.
  const lines = ['You are a coding assistant. Answer the user directly and concisely.'];
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

export class ChatViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private history: ChatMessage[] = [];
  private controller?: AbortController;
  private sessionId?: string;
  private sessionProvider?: string;
  private loginTerm?: vscode.Terminal;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly makeClient: () => SemaClient | undefined,
    private readonly repoRoot: string,
    private readonly sessionUsage: SessionUsage,
    private readonly refresh?: () => void,
  ) {}

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

  clearConversation(): void {
    this.history = [];
    this.sessionId = undefined;
    this.sessionProvider = undefined;
    this.sessionUsage.input = 0;
    this.sessionUsage.output = 0;
    this.sessionUsage.cached = 0;
    this.sessionUsage.cost = 0;
    this.sessionUsage.costKnown = false;
    this.sessionUsage.turns = 0;
    this.view?.webview.postMessage({ type: 'clear' });
    this.refresh?.();
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
        void this.refreshAuthState();
        break;
      case 'send':
        await this.handleSend(String(msg.text ?? ''));
        break;
      case 'stop':
        this.controller?.abort();
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
      case 'setEffort':
        await this.context.globalState.update(EFFORT_KEY, String(msg.effort));
        break;
      case 'customModel': {
        const entered = await vscode.window.showInputBox({
          title: 'Custom model id',
          prompt: `Enter a model id for ${getProvider(this.providerId).label}`,
          placeHolder: 'e.g. gpt-5.5 or claude-opus-4-8',
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
      context = client ? await this.buildContext(text, client) : '';
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
    let cliBin: string | undefined;
    if (provider.id === 'claude-code') {
      cliBin = cfg.get<string>('chat.claudePath');
    } else if (provider.id === 'codex') {
      cliBin = cfg.get<string>('chat.codexPath');
    }

    // Resume the session only if it belongs to the current provider; otherwise start fresh.
    const resumeId = this.sessionProvider === provider.id ? this.sessionId : undefined;
    let assistant = '';
    try {
      await provider.stream({
        apiKey,
        cwd: this.repoRoot,
        cliBin,
        agent,
        effort: this.effort,
        model: this.modelId,
        system: buildSystem(context, provider.readsWorkspace),
        messages: this.history,
        maxTokens,
        signal: this.controller.signal,
        sessionId: resumeId,
        onSession: (id) => {
          this.sessionId = id;
          this.sessionProvider = provider.id;
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
        // Hard failure — drop the user turn, and drop the session so the next turn
        // recovers with fresh, full history.
        this.history.pop();
        this.sessionId = undefined;
        this.sessionProvider = undefined;
        const message = (err as Error).message;
        view.webview.postMessage({ type: 'error', message });
        // Not signed in? Offer a one-click login for the CLI providers.
        if (
          provider.auth &&
          /not logged in|please run.*\/login|\/login|not authenticated|unauthorized/i.test(message)
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
    }
  }

  /** Retrieve relevant code via sema and format it as prompt context. */
  private async buildContext(query: string, client: SemaClient): Promise<string> {
    let results;
    try {
      results = await client.search(query, 8);
    } catch {
      return '';
    }
    if (!results.length) {
      return '';
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
    return parts.join('\n');
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
  body { margin: 0; padding: 0; font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); display: flex; flex-direction: column; height: 100vh; }
  #bar { display: flex; gap: 4px; align-items: center; padding: 6px; border-bottom: 1px solid var(--vscode-panel-border); flex-wrap: wrap; }
  #bar select, #bar button { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border: 1px solid var(--vscode-dropdown-border); border-radius: 4px; padding: 2px 6px; font-size: 12px; }
  #bar button { cursor: pointer; background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); border: none; }
  #bar button.warn { color: var(--vscode-editorWarning-foreground); }
  #bar button.ok { color: var(--vscode-testing-iconPassed, var(--vscode-foreground)); }
  #modelinfo { font-size: 11px; color: var(--vscode-descriptionForeground); align-self: center; }
  #messages { flex: 1; overflow-y: auto; padding: 8px; }
  .msg { margin-bottom: 10px; }
  .msg .bubble { padding: 8px 10px; border-radius: 8px; white-space: normal; word-wrap: break-word; }
  .msg.user .bubble { background: var(--vscode-textBlockQuote-background); border: 1px solid var(--vscode-panel-border); }
  .msg.assistant .bubble { background: var(--vscode-editor-inactiveSelectionBackground); }
  .msg.error .bubble { background: var(--vscode-inputValidation-errorBackground); border: 1px solid var(--vscode-inputValidation-errorBorder); }
  pre { background: var(--vscode-textCodeBlock-background); padding: 8px; border-radius: 6px; overflow-x: auto; }
  code { font-family: var(--vscode-editor-font-family); font-size: 12px; }
  .typing span { display: inline-block; width: 6px; height: 6px; margin-right: 3px; border-radius: 50%; background: var(--vscode-descriptionForeground); animation: sema-blink 1.2s infinite both; }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes sema-blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
  .trace { margin-bottom: 4px; }
  .act { font-size: 11px; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); padding: 1px 0; }
  details.think { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 4px; }
  details.think summary { cursor: pointer; user-select: none; }
  .think-body { white-space: pre-wrap; font-style: italic; opacity: .85; padding: 4px 0 4px 10px; }
  #status { padding: 0 8px; height: 14px; font-size: 11px; color: var(--vscode-descriptionForeground); }
  .notice { text-align: center; font-size: 11px; color: var(--vscode-descriptionForeground); padding: 4px 0; }
  #composer { display: flex; gap: 6px; padding: 6px; border-top: 1px solid var(--vscode-panel-border); align-items: flex-end; }
  #input { flex: 1; resize: none; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border); border-radius: 4px; padding: 6px; font-family: inherit; }
  #send { width: 28px; height: 28px; min-width: 28px; padding: 0; border: none; border-radius: 50%; background: var(--vscode-button-background); color: var(--vscode-button-foreground); cursor: pointer; display: flex; align-items: center; justify-content: center; }
  #send:hover { background: var(--vscode-button-hoverBackground); }
</style>
</head>
<body>
  <div id="bar">
    <select id="provider" title="Provider"></select>
    <select id="model" title="Model"></select>
    <select id="mode" title="Ask = read-only Q&amp;A · Agent = can edit files">
      <option value="ask">Ask</option>
      <option value="agent">Agent</option>
    </select>
    <select id="effort" title="Reasoning effort"></select>
    <button id="indexbtn" title="Use sema's semantic index as extra context (like Cursor's codebase context)">index: off</button>
    <button id="setkey" title="Set API key">Set key</button>
    <button id="loginbtn" title="Sign in to the selected CLI (Claude Code / Codex)">Log in</button>
    <button id="clear" title="Start a new chat (new session)">New chat</button>
    <span id="modelinfo" title="Model used for this chat"></span>
  </div>
  <div id="messages"></div>
  <div id="status"></div>
  <div id="composer">
    <textarea id="input" rows="2" placeholder="Ask about your codebase… (Enter to send, Shift+Enter for newline)"></textarea>
    <button id="send" title="Send"></button>
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
    var indexBtn = document.getElementById('indexbtn');
    var useIndex = false;
    function applyIndexBtn(){ indexBtn.textContent = useIndex ? 'index: on' : 'index: off'; indexBtn.className = useIndex ? 'ok' : ''; }
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
    function render(raw){
      var parts = raw.split(FENCE), html = '';
      for (var i=0;i<parts.length;i++){
        if (i % 2 === 1){
          var code = parts[i].replace(/^[a-zA-Z0-9_+.-]*\\n/, '');
          html += '<pre><code>' + esc(code) + '</code></pre>';
        } else {
          var t = esc(parts[i]);
          t = t.replace(new RegExp(BT + '([^' + BT + ']+)' + BT, 'g'), '<code>$1</code>');
          html += t.replace(/\\n/g, '<br>');
        }
      }
      return html;
    }
    function addBubble(role){
      var wrap = document.createElement('div'); wrap.className = 'msg ' + role;
      var b = document.createElement('div'); b.className = 'bubble'; wrap.appendChild(b);
      messagesEl.appendChild(wrap); messagesEl.scrollTop = messagesEl.scrollHeight; return b;
    }
    var ICON_SEND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V7"></path><path d="M6.5 12.5 L12 7 L17.5 12.5"></path></svg>';
    var ICON_STOP = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="3"></rect></svg>';
    function setStreaming(on){ streaming = on; sendBtn.innerHTML = on ? ICON_STOP : ICON_SEND; sendBtn.title = on ? 'Stop' : 'Send'; }
    function doSend(){
      if (streaming){ vscode.postMessage({type:'stop'}); return; }
      var text = input.value.trim(); if (!text) return;
      input.value = ''; setStreaming(true); vscode.postMessage({type:'send', text:text});
    }

    sendBtn.addEventListener('click', doSend);
    input.addEventListener('keydown', function(e){ if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); doSend(); } });
    providerSel.addEventListener('change', function(){ modelInfo.textContent=''; vscode.postMessage({type:'setProvider', provider:providerSel.value}); });
    modelSel.addEventListener('change', function(){ modelInfo.textContent=''; if (modelSel.value === '__custom__'){ vscode.postMessage({type:'customModel'}); } else { vscode.postMessage({type:'setModel', model:modelSel.value}); } });
    modeSel.addEventListener('change', function(){ vscode.postMessage({type:'setMode', mode:modeSel.value}); });
    effortSel.addEventListener('change', function(){ vscode.postMessage({type:'setEffort', effort:effortSel.value}); });
    indexBtn.addEventListener('click', function(){ useIndex = !useIndex; applyIndexBtn(); vscode.postMessage({type:'setIndex', value:useIndex}); });
    setkeyBtn.addEventListener('click', function(){ vscode.postMessage({type:'setKey'}); });
    loginBtn.addEventListener('click', function(){ vscode.postMessage({type: loggedInState ? 'logout' : 'login'}); });
    document.getElementById('clear').addEventListener('click', function(){ vscode.postMessage({type:'clear'}); });

    window.addEventListener('message', function(ev){
      var m = ev.data;
      if (m.type === 'config'){
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
        if (m.requiresKey) {
          setkeyBtn.style.display = '';
          setkeyBtn.textContent = m.hasKey ? 'Key set' : 'Set key';
          setkeyBtn.className = m.hasKey ? 'ok' : 'warn';
        } else {
          setkeyBtn.style.display = 'none';
        }
        if (!m.canLogin){ loginBtn.style.display = 'none'; loggedInState = false; } else { loginBtn.style.display = ''; }
      } else if (m.type === 'userMessage'){ addBubble('user').textContent = m.text; }
      else if (m.type === 'status'){ statusEl.textContent = m.text; }
      else if (m.type === 'assistantStart'){ if (revealTimer && answerEl && shownLen < answerRaw.length){ answerEl.innerHTML = render(answerRaw); } if (revealTimer){ clearInterval(revealTimer); revealTimer = null; } curEl = addBubble('assistant'); curEl.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>'; answerEl=null; traceEl=null; answerRaw=''; thinkBody=null; started=false; shownLen=0; setStreaming(true); statusEl.textContent = ''; }
      else if (m.type === 'thinking'){ ensureStructure(); if (!thinkBody){ var dt=document.createElement('details'); dt.className='think'; dt.open=true; var sm=document.createElement('summary'); sm.textContent='Thinking'; dt.appendChild(sm); thinkBody=document.createElement('div'); thinkBody.className='think-body'; dt.appendChild(thinkBody); traceEl.appendChild(dt); } thinkBody.textContent += m.text; messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'activity'){ ensureStructure(); var ac=document.createElement('div'); ac.className='act'; ac.textContent = '⚙ ' + m.tool + (m.detail ? '  ' + m.detail : ''); traceEl.appendChild(ac); messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'delta'){ ensureStructure(); answerRaw += m.text; startReveal(); }
      else if (m.type === 'assistantEnd'){ if (curEl && !started){ curEl.textContent = '(no output)'; } statusEl.textContent = ''; setStreaming(false); curEl = null; }
      else if (m.type === 'error'){ addBubble('error').textContent = m.message; setStreaming(false); statusEl.textContent = ''; }
      else if (m.type === 'clear'){ messagesEl.innerHTML = ''; modelInfo.textContent = ''; }
      else if (m.type === 'model'){ modelInfo.textContent = '→ ' + m.model; }
      else if (m.type === 'notice'){ var nt=document.createElement('div'); nt.className='notice'; nt.textContent = m.text; messagesEl.appendChild(nt); messagesEl.scrollTop = messagesEl.scrollHeight; }
      else if (m.type === 'auth'){ if (!m.canLogin){ loginBtn.style.display='none'; loggedInState=false; } else { loginBtn.style.display=''; loggedInState=!!m.loggedIn; loginBtn.textContent = m.loggedIn ? '✓ Signed in' : 'Log in'; loginBtn.className = m.loggedIn ? 'ok' : 'warn'; loginBtn.title = m.loggedIn ? 'Signed in — click to sign out' : 'Sign in to the selected CLI (Claude Code / Codex)'; } }
    });

    setStreaming(false);
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
