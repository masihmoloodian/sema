export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  /** Portion of inputTokens re-read from cache (cheap); the rest is fresh input. */
  cachedInputTokens?: number;
  /** Reported cost in USD (Claude); omitted when the CLI doesn't report it (Codex). */
  costUsd?: number;
}

export interface StreamOptions {
  model: string;
  system: string;
  messages: ChatMessage[];
  maxTokens: number;
  signal: AbortSignal;
  onDelta: (text: string) => void;
  /** Streamed model reasoning (thinking blocks). */
  onThinking?: (text: string) => void;
  /** A tool the model invoked, e.g. tool "Read", detail "package.json". */
  onActivity?: (tool: string, detail: string) => void;
  /** For API-key providers (Anthropic / OpenAI). */
  apiKey?: string;
  /** Working directory for CLI providers (claude / codex). */
  cwd?: string;
  /** Override the CLI executable path (claude / codex) when not on PATH. */
  cliBin?: string;
  /** Agent mode: let CLI providers edit files instead of answering read-only. */
  agent?: boolean;
  /** Plan mode: investigate read-only and propose a step-by-step plan (no edits). */
  plan?: boolean;
  /** Path to the sema binary — enables the semantic search_code / get_code tools in agent/plan mode. */
  semaBin?: string;
  /** Reasoning effort level ('default' = the CLI's own default). */
  effort?: string;
  /** Resume an existing CLI session (memory across turns); omit to start fresh. */
  sessionId?: string;
  /** Reports the CLI session id once known, so the next turn can resume it. */
  onSession?: (id: string) => void;
  /** Reports the actual model the CLI used (resolves 'default'). */
  onModel?: (model: string) => void;
  /** Reports token usage (and cost when available) for the turn. */
  onUsage?: (usage: TokenUsage) => void;
}

export interface ChatProvider {
  readonly id: string;
  readonly label: string;
  readonly models: string[];
  readonly defaultModel: string;
  /** Reasoning effort levels this provider supports; 'default' first. Single entry = no effort control. */
  readonly efforts: string[];
  /** True for API-key providers; false for local-CLI providers that reuse an existing login. */
  readonly requiresKey: boolean;
  /** True if the provider reads the repo itself (agentic CLI); false if it needs injected RAG context. */
  readonly readsWorkspace: boolean;
  /** SecretStorage key — only for key providers. */
  readonly secretKey?: string;
  /** Where the user obtains a key — only for key providers. */
  readonly keyHint?: string;
  /** Example model id(s) shown in the custom-model-id prompt — hints the expected format. */
  readonly modelHint?: string;
  /** CLI auth verbs (args for the provider's CLI). Absent for API-key providers. */
  readonly auth?: { login: string[]; logout: string[]; status: string[] };
  stream(opts: StreamOptions): Promise<void>;
}
