import { promises as fs } from 'fs';
import * as path from 'path';
import { Attachment, AttachmentKind, ChatMessage } from './providers/types';

/**
 * File attachments — sniffing, limits, on-disk staging, and the degradation gate that
 * decides what each model actually receives.
 *
 * Deliberately free of `vscode` imports so it stays unit-testable, and free of any
 * webview-supplied path handling: bytes are staged as `<dir>/<id>` and `id` is minted
 * here, so a hostile filename can never escape the directory or point a CLI's
 * `--image` flag at an arbitrary file.
 */

/**
 * Per-file byte caps (raw, pre-base64).
 *
 * The PDF cap is 20MB rather than Anthropic's documented 32MB because that 32MB is a
 * whole-request limit and base64 inflates by 4/3 — a 32MB PDF encodes to ~43MB and can
 * never succeed. TOTAL is the same budget applied across the transcript, since every
 * provider re-sends the full history on each turn.
 */
export const LIMITS = {
  image: 5 * 1024 * 1024,
  pdf: 20 * 1024 * 1024,
  text: 256 * 1024,
  total: 20 * 1024 * 1024,
} as const;

/** Media types Anthropic and OpenAI both accept for image blocks. */
const IMAGE_MIME: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

/** Extensions we treat as plain text. Anything else non-image/pdf is rejected. */
const TEXT_EXT = new Set([
  '.txt', '.md', '.markdown', '.rst', '.log', '.csv', '.tsv',
  '.json', '.jsonc', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env',
  '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.py', '.go', '.rs', '.rb', '.php',
  '.java', '.kt', '.swift', '.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.scala', '.sh',
  '.bash', '.zsh', '.fish', '.sql', '.html', '.css', '.scss', '.less', '.xml', '.svg',
  '.diff', '.patch', '.gradle', '.tf', '.dockerfile', '.makefile', '.lua', '.pl', '.r',
]);

export interface Sniffed {
  kind: AttachmentKind;
  mime: string;
}

/** True when `bytes` starts with the given byte sequence. */
function startsWith(bytes: Uint8Array, sig: readonly number[]): boolean {
  if (bytes.length < sig.length) {
    return false;
  }
  return sig.every((b, i) => bytes[i] === b);
}

/**
 * Classify a file by magic bytes first, extension second. Magic bytes win because the
 * extension is attacker-controlled (a drag-dropped `shot.png` that is really a PDF must
 * be sent as a PDF, or the provider 400s on a media_type mismatch).
 */
export function sniff(name: string, bytes: Uint8Array): Sniffed | undefined {
  if (startsWith(bytes, [0x89, 0x50, 0x4e, 0x47])) {
    return { kind: 'image', mime: 'image/png' };
  }
  if (startsWith(bytes, [0xff, 0xd8, 0xff])) {
    return { kind: 'image', mime: 'image/jpeg' };
  }
  if (startsWith(bytes, [0x47, 0x49, 0x46, 0x38])) {
    return { kind: 'image', mime: 'image/gif' };
  }
  // RIFF....WEBP
  if (startsWith(bytes, [0x52, 0x49, 0x46, 0x46]) && startsWith(bytes.subarray(8), [0x57, 0x45, 0x42, 0x50])) {
    return { kind: 'image', mime: 'image/webp' };
  }
  if (startsWith(bytes, [0x25, 0x50, 0x44, 0x46])) {
    return { kind: 'pdf', mime: 'application/pdf' };
  }

  const ext = path.extname(name).toLowerCase();
  if (IMAGE_MIME[ext]) {
    return { kind: 'image', mime: IMAGE_MIME[ext] };
  }
  if (ext === '.pdf') {
    return { kind: 'pdf', mime: 'application/pdf' };
  }
  // Extension-listed, or extensionless-but-not-binary (Makefile, Dockerfile, LICENSE).
  if (TEXT_EXT.has(ext) || (ext === '' && !looksBinary(bytes))) {
    return { kind: 'text', mime: 'text/plain' };
  }
  return undefined;
}

/** A NUL byte in the first 8KB is the classic "this is not text" heuristic. */
function looksBinary(bytes: Uint8Array): boolean {
  return bytes.subarray(0, 8192).includes(0);
}

/** Human-readable size for chips and error messages. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Reject an over-cap file, naming the limit it broke. Returns undefined when fine. */
export function checkLimit(kind: AttachmentKind, size: number): string | undefined {
  const cap = LIMITS[kind];
  if (size > cap) {
    return `${formatSize(size)} exceeds the ${formatSize(cap)} limit for ${kind} attachments`;
  }
  return undefined;
}

/** Canonical extension per sniffed media type. */
const MIME_EXT: Record<string, string> = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/gif': '.gif',
  'image/webp': '.webp',
  'application/pdf': '.pdf',
};

/**
 * The extension a staged file should carry.
 *
 * It matters: tools downstream classify by extension, not just by magic bytes — an
 * extensionless image handed to `opencode -f` is read as an opaque file rather than
 * shown to the model. Derived from the *sniffed* mime (or checked against the text
 * allow-list), never taken raw from the user-supplied name, so it stays path-safe.
 */
function extFor(kind: AttachmentKind, mime: string, name: string): string {
  if (kind === 'text') {
    const ext = path.extname(name).toLowerCase();
    return TEXT_EXT.has(ext) ? ext : '.txt';
  }
  return MIME_EXT[mime] ?? '';
}

let seq = 0;

/** Mint an id that is safe as a filename and unique within a session. */
function newId(ext: string): string {
  seq += 1;
  return `${Date.now().toString(36)}-${seq.toString(36)}-${Math.random().toString(36).slice(2, 8)}${ext}`;
}

export function pathFor(dir: string, id: string): string {
  return path.join(dir, id);
}

/**
 * Write bytes into the session's attachment directory and return the metadata record.
 * The on-disk name is the minted id, never the user-supplied `name`.
 */
export async function stage(
  dir: string,
  name: string,
  bytes: Uint8Array,
): Promise<{ attachment: Attachment } | { error: string }> {
  const sniffed = sniff(name, bytes);
  if (!sniffed) {
    return { error: `${name}: unsupported file type — attach an image, a PDF, or a text file` };
  }
  const limit = checkLimit(sniffed.kind, bytes.byteLength);
  if (limit) {
    return { error: `${name}: ${limit}` };
  }
  const id = newId(extFor(sniffed.kind, sniffed.mime, name));
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(pathFor(dir, id), bytes);
  return {
    attachment: {
      id,
      name: path.basename(name),
      kind: sniffed.kind,
      mime: sniffed.mime,
      size: bytes.byteLength,
    },
  };
}

/** Remove a staged file — used when a caller rejects an attachment after staging it. */
export async function unstage(dir: string, id: string): Promise<void> {
  await fs.rm(pathFor(dir, id), { force: true });
}

/** Base64 of a staged file, with no newlines — the shape Anthropic requires. */
export async function readBase64(dir: string, att: Attachment): Promise<string> {
  const buf = await fs.readFile(pathFor(dir, att.id));
  return buf.toString('base64');
}

/** Staged text, truncated at the text cap with an explicit marker. */
export async function readText(dir: string, att: Attachment): Promise<string> {
  const buf = await fs.readFile(pathFor(dir, att.id));
  const text = buf.toString('utf8');
  return text.length > LIMITS.text
    ? `${text.slice(0, LIMITS.text)}\n… (truncated)`
    : text;
}

/** Data URI. OpenAI needs this shape for both `image_url.url` and `file.file_data`. */
export function toDataUri(mime: string, base64: string): string {
  return `data:${mime};base64,${base64}`;
}

/** Fenced block for a text attachment, inlined into the turn's prompt. */
export function textBlock(name: string, body: string): string {
  const fence = '```';
  return `${fence} ${name}\n${body}\n${fence}`;
}

/** Total raw bytes of every attachment in a transcript. */
export function totalBytes(messages: readonly ChatMessage[]): number {
  return messages.reduce(
    (sum, m) => sum + (m.attachments ?? []).reduce((s, a) => s + a.size, 0),
    0,
  );
}

export interface MaterializeResult {
  messages: ChatMessage[];
  /** User-facing notes about attachments that were degraded rather than sent. */
  warnings: string[];
}

/**
 * The single gate every turn passes through before it reaches a provider.
 *
 * Two jobs, both of which have to happen in one pass over the whole transcript:
 *  - inline text attachments into `content` (universal — every model reads text), and
 *  - replace any image/pdf the target model can't read with a text placeholder.
 *
 * The second is what makes switching provider mid-conversation safe: the history still
 * holds an image attached while Anthropic was selected, and replaying that to a
 * text-only model would 400. Callers block *new* unsupported attachments up front; this
 * handles the historical ones.
 *
 * Providers therefore only ever see image/pdf attachments they are known to support.
 */
export async function materialize(
  messages: readonly ChatMessage[],
  opts: {
    dir: string;
    accepts: readonly AttachmentKind[];
    /** Why a dropped attachment was dropped, for the placeholder and the warning. */
    reason?: string;
  },
): Promise<MaterializeResult> {
  const reason = opts.reason ?? "not supported by this model";
  const warnings: string[] = [];
  const out: ChatMessage[] = [];

  for (const m of messages) {
    const atts = m.attachments ?? [];
    if (!atts.length) {
      out.push(m);
      continue;
    }

    const kept: Attachment[] = [];
    const appended: string[] = [];
    for (const a of atts) {
      if (a.kind === 'text') {
        try {
          appended.push(textBlock(a.name, await readText(opts.dir, a)));
        } catch {
          appended.push(`[${a.name} — attachment file is missing]`);
        }
        continue;
      }
      if (opts.accepts.includes(a.kind)) {
        kept.push(a);
        continue;
      }
      appended.push(`[${a.kind}: ${a.name} — ${reason}, omitted]`);
      warnings.push(`${a.name} was omitted — ${reason}.`);
    }

    const content = [m.content, ...appended].filter((p) => p.trim()).join('\n\n');
    out.push({ ...m, content, attachments: kept.length ? kept : undefined });
  }

  return { messages: out, warnings };
}
