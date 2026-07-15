import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import { ChatMessage } from './providers';
import { SessionUsage } from './semaClient';

/** A full, persisted chat session — the transcript plus everything needed to resume it. */
export interface StoredSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  /** Last provider/model used in this session (for display and CLI-resume matching). */
  provider: string;
  model: string;
  /** CLI resume handle (Claude Code / Codex) + the provider it belongs to. */
  cliSessionId?: string;
  cliSessionProvider?: string;
  usage: SessionUsage;
  messages: ChatMessage[];
}

/** Lightweight row for the history browser — everything but the full transcript. */
export interface SessionMeta {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  provider: string;
  model: string;
  messageCount: number;
}

/** A zeroed usage tally — for a brand-new session or after "New chat". */
export function freshUsage(): SessionUsage {
  return { input: 0, output: 0, cached: 0, cost: 0, costKnown: false, turns: 0 };
}

function newId(): string {
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
}

/** Build a new (unsaved) session in memory — written to disk on its first `save`. */
export function createSession(provider: string, model: string): StoredSession {
  const now = Date.now();
  return {
    id: newId(),
    title: 'New chat',
    createdAt: now,
    updatedAt: now,
    provider,
    model,
    usage: freshUsage(),
    messages: [],
  };
}

/** Derive a one-line title from the first user message (used until a session is named). */
export function titleFromMessages(messages: ChatMessage[]): string {
  const first = messages.find(
    (m) => m.role === 'user' && (m.content.trim() || m.attachments?.length),
  );
  if (!first) {
    return 'New chat';
  }
  const line = first.content.trim().split('\n')[0].trim();
  if (!line) {
    // A turn that was only an attachment — name it after the file, else the history
    // browser would show "New chat" for this session forever.
    const att = first.attachments?.[0];
    return att ? `📎 ${att.name}` : 'New chat';
  }
  return line.length > 60 ? line.slice(0, 60).trimEnd() + '…' : line;
}

/**
 * Per-workspace, on-disk store of chat sessions — one JSON file per session under
 * `<globalStorage>/sessions/<workspace-hash>/`. Survives VS Code restarts and is
 * partitioned by repo so each project keeps its own chat history (like Claude Code).
 */
export class SessionStore {
  private readonly dir: string;

  constructor(baseDir: string, workspaceKey: string) {
    const hash = crypto
      .createHash('sha1')
      .update(workspaceKey || '_noworkspace')
      .digest('hex')
      .slice(0, 16);
    this.dir = path.join(baseDir, 'sessions', hash);
  }

  private ensureDir(): void {
    fs.mkdirSync(this.dir, { recursive: true });
  }

  private fileFor(id: string): string {
    return path.join(this.dir, `${id}.json`);
  }

  /**
   * Where a session's attached files are staged. Lives inside the per-workspace
   * partition so a stray directory can always be traced back to its repo — a flat
   * global dir would leave orphans whose workspace is unknowable.
   */
  attachmentsDir(id: string): string {
    return path.join(this.dir, 'attachments', id);
  }

  /**
   * Drop attachment directories with no surviving session. Covers the cases the normal
   * delete path can't: a "New chat" abandoned after staging a file, and the hard-error
   * path in the chat panel that pops the turn. The 24h floor keeps it from racing a
   * session that has staged files but hasn't been written to disk yet.
   */
  gcOrphans(): void {
    const root = path.join(this.dir, 'attachments');
    let ids: string[];
    try {
      ids = fs.readdirSync(root);
    } catch {
      return; // nothing staged yet
    }
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    for (const id of ids) {
      try {
        if (fs.existsSync(this.fileFor(id))) {
          continue;
        }
        if (fs.statSync(path.join(root, id)).mtimeMs > cutoff) {
          continue;
        }
        fs.rmSync(path.join(root, id), { recursive: true, force: true });
      } catch {
        // Best-effort cleanup — never let it break startup.
      }
    }
  }

  /** All sessions, newest activity first — reads and parses every session file. */
  list(): SessionMeta[] {
    let names: string[];
    try {
      names = fs.readdirSync(this.dir).filter((n) => n.endsWith('.json'));
    } catch {
      return [];
    }
    const metas: SessionMeta[] = [];
    for (const name of names) {
      try {
        const raw = fs.readFileSync(path.join(this.dir, name), 'utf8');
        const s = JSON.parse(raw) as StoredSession;
        if (!s || !s.id) {
          continue;
        }
        metas.push({
          id: s.id,
          title: s.title || titleFromMessages(s.messages ?? []),
          createdAt: s.createdAt ?? 0,
          updatedAt: s.updatedAt ?? s.createdAt ?? 0,
          provider: s.provider ?? '',
          model: s.model ?? '',
          messageCount: (s.messages ?? []).length,
        });
      } catch {
        // Skip a corrupt/half-written file rather than failing the whole list.
      }
    }
    metas.sort((a, b) => b.updatedAt - a.updatedAt);
    return metas;
  }

  load(id: string): StoredSession | undefined {
    try {
      const raw = fs.readFileSync(this.fileFor(id), 'utf8');
      const s = JSON.parse(raw) as StoredSession;
      if (!s.usage) {
        s.usage = freshUsage();
      }
      if (!Array.isArray(s.messages)) {
        s.messages = [];
      }
      return s;
    } catch {
      return undefined;
    }
  }

  /** Write a session to disk atomically (temp file + rename). */
  save(session: StoredSession): void {
    this.ensureDir();
    const tmp = this.fileFor(session.id) + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(session), 'utf8');
    fs.renameSync(tmp, this.fileFor(session.id));
  }

  delete(id: string): void {
    try {
      fs.unlinkSync(this.fileFor(id));
    } catch {
      // Already gone — nothing to do.
    }
    try {
      fs.rmSync(this.attachmentsDir(id), { recursive: true, force: true });
    } catch {
      // Best-effort — gcOrphans will sweep it later.
    }
  }
}
