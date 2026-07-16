/** What a file attached to a turn is, from the model's point of view. */
export type AttachmentKind = 'image' | 'pdf' | 'text';

/**
 * A file attached to a chat turn — metadata only. The bytes live on disk under the
 * session's attachment directory, named by `id`, and are read at send time. Keeping
 * them out of this object is what lets a session (and so SessionStore.list, which
 * parses every session file on every turn) stay small.
 */
export interface Attachment {
  /** Stable id; also the on-disk filename. Never derived from `name`. */
  id: string;
  /** Original filename — shown in the composer, and sent to OpenAI as `filename`. */
  name: string;
  kind: AttachmentKind;
  mime: string;
  /** Raw (pre-base64) size in bytes. */
  size: number;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  /** Files attached to this turn. Absent on assistant turns. */
  attachments?: Attachment[];
}

/** Rich metadata for one selectable model, powering the grouped model picker. */
export interface ModelInfo {
  /** Value passed to the provider/CLI — a model id or a CLI alias (e.g. 'opus'). */
  id: string;
  /** Friendly display name shown in the picker; falls back to id. */
  name?: string;
  /** Optional CLI alias, for reference. */
  alias?: string;
  /** One-line description shown as the option's tooltip. */
  description?: string;
  /** Marks the recommended/default model (rendered with a ⭐). */
  recommended?: boolean;
  /** Optional group label; models sharing one render under an <optgroup>. */
  section?: string;
  /**
   * Attachment kinds this specific model can read, overriding the provider's default.
   * Set it when one model in a provider's list differs from the rest (e.g. a text-only
   * model on a provider whose other models have vision).
   */
  accepts?: readonly AttachmentKind[];
  /** Reasoning levels supported by this model when they differ within a provider. */
  efforts?: readonly string[];
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
  /**
   * Directory holding this session's staged attachment files (named by `Attachment.id`).
   * Providers resolve bytes/paths from here — see attachments.ts. Only image/pdf
   * attachments reach a provider; text is inlined into `content` before the call.
   */
  attachmentsDir?: string;
}

export interface ChatProvider {
  readonly id: string;
  readonly label: string;
  /** Flat list of model ids (derived from modelInfos) — used for validation. */
  readonly models: string[];
  /** Rich model metadata (names, sections, recommended) for the picker. */
  readonly modelInfos: ModelInfo[];
  readonly defaultModel: string;
  /**
   * Reasoning-effort levels this provider's CLI accepts, 'default' first ('default' =
   * pass no flag and let the CLI choose). Absent when the provider has no effort
   * control at all — only the local Claude Code and Codex CLIs expose one, and their
   * accepted values differ, so this list must mirror the CLI's own argument.
   */
  readonly efforts?: readonly string[];
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
  /**
   * Attachment kinds `model` can read. Per-model rather than per-provider: users add
   * arbitrary custom model ids, and OpenRouter/Together list hundreds, so a single
   * provider-wide flag would promise vision on text-only models. 'text' is always
   * included — it inlines into the prompt, which every model can read.
   */
  accepts(model: string): readonly AttachmentKind[];
  stream(opts: StreamOptions): Promise<void>;
}
