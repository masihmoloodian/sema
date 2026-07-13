import { execFile } from 'child_process';

/**
 * PII redaction — the hybrid used by the chat panel's "redact" toggle.
 *
 * `redactPatterns` is the fast, offline, always-on layer: regex for structured
 * PII and secrets (emails, cards, SSNs, API keys/tokens, private keys). It runs
 * in the extension host with zero dependencies. `redactViaSema` adds the model
 * layer — it pipes text through `sema redact` (spaCy NER) to also catch person
 * and location names — and degrades to null (patterns-only) when the optional
 * PII extra isn't installed.
 */

export interface RedactResult {
  text: string;
  /** Count of redactions by placeholder kind, e.g. { EMAIL: 2, NAME: 1 }. */
  found: Record<string, number>;
  /** True if the spaCy NER pass actually ran (names/locations covered). */
  nerRan: boolean;
}

const PATTERNS: { re: RegExp; label: string; luhn?: boolean }[] = [
  { re: /-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----/g, label: 'PRIVATE_KEY' },
  { re: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, label: 'JWT' },
  { re: /\bsk-[A-Za-z0-9_-]{16,}\b/g, label: 'API_KEY' }, // OpenAI / Anthropic / OpenRouter
  { re: /\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b/g, label: 'API_KEY' }, // Stripe
  { re: /\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b/g, label: 'API_KEY' }, // GitHub
  { re: /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g, label: 'API_KEY' }, // Slack
  { re: /\bAKIA[0-9A-Z]{16}\b/g, label: 'API_KEY' }, // AWS access key id
  { re: /\bAIza[0-9A-Za-z_-]{35}\b/g, label: 'API_KEY' }, // Google
  { re: /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g, label: 'EMAIL' },
  { re: /\b\d(?:[ -]?\d){12,18}\b/g, label: 'CREDIT_CARD', luhn: true },
  { re: /\b\d{3}-\d{2}-\d{4}\b/g, label: 'SSN' },
  { re: /(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b/g, label: 'PHONE' },
];

function luhnOk(candidate: string): boolean {
  const digits = candidate.replace(/\D/g, '');
  if (digits.length < 13 || digits.length > 19) {
    return false;
  }
  let sum = 0;
  let alt = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (alt) {
      d *= 2;
      if (d > 9) {
        d -= 9;
      }
    }
    sum += d;
    alt = !alt;
  }
  return sum % 10 === 0;
}

// Self-introductions where the name is often lowercase — the NER model keys on
// capitalization and misses those, so catch them here. Only the name is redacted;
// the lead-in phrase ("my name is") is kept.
// Case-sensitive on purpose: the lead-in phrases spell both cases explicitly, so
// the optional second name token can require a real capital (an /i flag would let
// it swallow a following lowercase word like "and").
const NAME_INTRO =
  /\b([Mm]y name is|[Mm]y name's|[Ii] go by|[Ii]'?m called)(\s+)([A-Za-z][A-Za-z'.-]*(?:\s+[A-Z][A-Za-z'.-]*)?)/g;

/** Regex redaction of structured PII and secrets — instant, offline, no deps. */
export function redactPatterns(input: string): { text: string; found: Record<string, number> } {
  const found: Record<string, number> = {};
  let text = input;
  text = text.replace(NAME_INTRO, (_m, lead: string, sp: string) => {
    found.NAME = (found.NAME || 0) + 1;
    return `${lead}${sp}[NAME]`;
  });
  for (const { re, label, luhn } of PATTERNS) {
    text = text.replace(re, (m) => {
      if (luhn && !luhnOk(m)) {
        return m; // number that isn't a valid card — leave it
      }
      found[label] = (found[label] || 0) + 1;
      return `[${label}]`;
    });
  }
  return { text, found };
}

/** Run `sema redact --json` over text (via stdin). Returns null if NER is unavailable. */
function redactViaSema(
  text: string,
  semaBin: string,
  cwd: string,
  signal?: AbortSignal,
): Promise<{ text: string; found: Record<string, number> } | null> {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v: { text: string; found: Record<string, number> } | null): void => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
    let child;
    try {
      child = execFile(
        semaBin,
        ['redact', '--json'],
        { cwd, timeout: 20000, maxBuffer: 16 * 1024 * 1024, signal },
        (err, stdout) => {
          if (err) {
            done(null);
            return;
          }
          try {
            const data = JSON.parse(stdout) as {
              redacted?: boolean;
              text?: string;
              entities?: { type?: string; count?: number }[];
            };
            if (!data || data.redacted === false || typeof data.text !== 'string') {
              done(null);
              return;
            }
            const found: Record<string, number> = {};
            for (const e of data.entities ?? []) {
              const key = String(e.type ?? '').replace(/[[\]]/g, '');
              if (key) {
                found[key] = (found[key] || 0) + (e.count ?? 0);
              }
            }
            done({ text: data.text, found });
          } catch {
            done(null);
          }
        },
      );
    } catch {
      done(null);
      return;
    }
    child.on('error', () => done(null));
    try {
      child.stdin?.end(text);
    } catch {
      done(null);
    }
  });
}

/** Regex layer, then the spaCy NER layer on top (if the sema binary is available). */
export async function redactAll(
  input: string,
  opts: { semaBin?: string; cwd: string; signal?: AbortSignal },
): Promise<RedactResult> {
  const first = redactPatterns(input);
  const found: Record<string, number> = { ...first.found };
  let text = first.text;
  let nerRan = false;
  if (opts.semaBin && text.trim()) {
    const ner = await redactViaSema(text, opts.semaBin, opts.cwd, opts.signal);
    if (ner) {
      text = ner.text;
      nerRan = true;
      for (const [k, v] of Object.entries(ner.found)) {
        found[k] = (found[k] || 0) + v;
      }
    }
  }
  return { text, found, nerRan };
}

// Private-use sentinel to batch many pieces through one NER call; regex won't
// match it and spaCy won't treat it as an entity, so it survives redaction.
const SEP = '\nSEMASEP\n';

/** Redact several pieces (context + messages) in a single pass, preserving boundaries. */
export async function redactPieces(
  pieces: string[],
  opts: { semaBin?: string; cwd: string; signal?: AbortSignal },
): Promise<{ pieces: string[]; found: Record<string, number>; nerRan: boolean }> {
  const { text, found, nerRan } = await redactAll(pieces.join(SEP), opts);
  const out = text.split(SEP);
  if (out.length === pieces.length) {
    return { pieces: out, found, nerRan };
  }
  // Sentinel didn't survive (shouldn't happen) — fall back to per-piece regex only.
  const safe = pieces.map((p) => redactPatterns(p));
  const f: Record<string, number> = {};
  for (const s of safe) {
    for (const [k, v] of Object.entries(s.found)) {
      f[k] = (f[k] || 0) + v;
    }
  }
  return { pieces: safe.map((s) => s.text), found: f, nerRan: false };
}

/** One-line human summary of a `found` map, e.g. "2 emails, 1 API key". */
export function redactionSummary(found: Record<string, number>): string {
  const names: Record<string, string> = {
    EMAIL: 'email',
    PHONE: 'phone number',
    SSN: 'SSN',
    CREDIT_CARD: 'card number',
    API_KEY: 'API key',
    JWT: 'token',
    PRIVATE_KEY: 'private key',
    NAME: 'name',
    LOCATION: 'location',
  };
  const parts = Object.entries(found)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => {
      const label = names[k] ?? k.toLowerCase();
      return `${n} ${label}${n > 1 ? 's' : ''}`;
    });
  return parts.join(', ');
}
