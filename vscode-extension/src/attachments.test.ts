import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { promises as fs } from 'fs';
import * as os from 'os';
import * as path from 'path';
import { ChatMessage } from './providers/types';
import { LIMITS, checkLimit, materialize, sniff, stage, toDataUri, totalBytes } from './attachments';

/**
 * Unit tests for the pure/filesystem half of attachment handling. Run with:
 *   npm test
 * `attachments.ts` deliberately imports no `vscode`, which is what lets this run under
 * plain `node --test` with no editor harness.
 */

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);
const PDF = Buffer.from('%PDF-1.4\n trailer\n%%EOF\n');

async function tmpdir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), 'sema-att-'));
}

test('sniff identifies types by magic bytes', () => {
  assert.deepEqual(sniff('x.png', PNG), { kind: 'image', mime: 'image/png' });
  assert.deepEqual(sniff('x.pdf', PDF), { kind: 'pdf', mime: 'application/pdf' });
  assert.deepEqual(sniff('notes.md', Buffer.from('# hi')), { kind: 'text', mime: 'text/plain' });
});

test('sniff trusts magic bytes over a lying extension', () => {
  // A PDF named .png must be sent as a PDF — the extension is attacker-controlled, and
  // a media_type that disagrees with the bytes is a provider-side 400.
  assert.deepEqual(sniff('actually-a.png', PDF), { kind: 'pdf', mime: 'application/pdf' });
});

test('sniff rejects unknown binary', () => {
  assert.equal(sniff('a.bin', Buffer.from([0x00, 0x01, 0x02, 0x03])), undefined);
  assert.equal(sniff('a.zip', Buffer.from('PKbinary')), undefined);
});

test('sniff accepts extensionless text but not extensionless binary', () => {
  assert.deepEqual(sniff('Makefile', Buffer.from('all:\n\tgo build')), {
    kind: 'text',
    mime: 'text/plain',
  });
  assert.equal(sniff('blob', Buffer.from([0x41, 0x00, 0x42])), undefined);
});

test('checkLimit enforces the per-kind cap', () => {
  assert.equal(checkLimit('image', 1024), undefined);
  assert.match(String(checkLimit('image', LIMITS.image + 1)), /exceeds the 5.0 MB limit/);
  // The PDF cap is below Anthropic's 32MB request limit because base64 inflates by 4/3.
  assert.equal(LIMITS.pdf, 20 * 1024 * 1024);
});

test('stage writes under the id, never the supplied name', async () => {
  const dir = await tmpdir();
  const res = await stage(dir, '../../../escape.png', PNG);
  assert.ok('attachment' in res, 'expected staging to succeed');
  const att = res.attachment;

  assert.equal(att.name, 'escape.png', 'display name is basenamed');
  assert.ok(!att.id.includes('/') && !att.id.includes('..'), `id must be path-safe: ${att.id}`);

  // The only thing written is <dir>/<id> — nothing escaped the directory.
  assert.deepEqual(await fs.readdir(dir), [att.id]);
  assert.deepEqual(await fs.readFile(path.join(dir, att.id)), PNG);
});

test('stage gives the staged file a canonical extension', async () => {
  // Downstream tools classify by extension: an extensionless image handed to
  // `opencode -f` is treated as an opaque file and never shown to the model.
  const dir = await tmpdir();
  const png = await stage(dir, 'shot.png', PNG);
  const pdf = await stage(dir, 'paper.pdf', PDF);
  const txt = await stage(dir, 'notes.md', Buffer.from('# hi'));
  assert.ok('attachment' in png && 'attachment' in pdf && 'attachment' in txt);

  assert.ok(png.attachment.id.endsWith('.png'), png.attachment.id);
  assert.ok(pdf.attachment.id.endsWith('.pdf'), pdf.attachment.id);
  assert.ok(txt.attachment.id.endsWith('.md'), txt.attachment.id);
});

test('staged extension comes from the sniffed mime, not the claimed name', async () => {
  const dir = await tmpdir();
  // A PDF wearing a .png name must land on disk as .pdf, or a downstream tool
  // classifies it by the lie.
  const res = await stage(dir, 'actually-a.png', PDF);
  assert.ok('attachment' in res);
  assert.ok(res.attachment.id.endsWith('.pdf'), res.attachment.id);
  assert.equal(res.attachment.mime, 'application/pdf');
});

test('a hostile name cannot inject a path via the extension', async () => {
  const dir = await tmpdir();
  const res = await stage(dir, '../../evil/x.png', PNG);
  assert.ok('attachment' in res);
  assert.ok(!res.attachment.id.includes('/'), res.attachment.id);
  assert.deepEqual(await fs.readdir(dir), [res.attachment.id]);
});

test('an unrecognised text extension falls back to .txt', async () => {
  const dir = await tmpdir();
  const res = await stage(dir, 'Makefile', Buffer.from('all:\n\tgo build'));
  assert.ok('attachment' in res);
  assert.ok(res.attachment.id.endsWith('.txt'), res.attachment.id);
});

test('stage rejects an oversized file and an unknown type', async () => {
  const dir = await tmpdir();
  const big = Buffer.concat([PNG, Buffer.alloc(LIMITS.image)]);
  const over = await stage(dir, 'big.png', big);
  assert.ok('error' in over && /exceeds/.test(over.error));

  const bad = await stage(dir, 'x.zip', Buffer.from('PKjunk'));
  assert.ok('error' in bad && /unsupported file type/.test(bad.error));

  assert.deepEqual(await fs.readdir(dir), [], 'nothing is staged when validation fails');
});

test('toDataUri builds the shape OpenAI requires', () => {
  assert.equal(toDataUri('image/png', 'AAAA'), 'data:image/png;base64,AAAA');
});

test('materialize inlines text attachments into the prompt', async () => {
  const dir = await tmpdir();
  const staged = await stage(dir, 'config.yml', Buffer.from('port: 8080'));
  assert.ok('attachment' in staged);

  const msgs: ChatMessage[] = [
    { role: 'user', content: 'what port?', attachments: [staged.attachment] },
  ];
  const out = await materialize(msgs, { dir, accepts: ['text'] });

  assert.match(out.messages[0].content, /what port\?/);
  assert.match(out.messages[0].content, /port: 8080/);
  assert.equal(out.messages[0].attachments, undefined, 'text never reaches a provider');
  assert.deepEqual(out.warnings, []);
});

test('materialize keeps attachments the model accepts', async () => {
  const dir = await tmpdir();
  const staged = await stage(dir, 'shot.png', PNG);
  assert.ok('attachment' in staged);

  const msgs: ChatMessage[] = [{ role: 'user', content: 'look', attachments: [staged.attachment] }];
  const out = await materialize(msgs, { dir, accepts: ['image', 'text'] });

  assert.equal(out.messages[0].attachments?.length, 1);
  assert.equal(out.messages[0].content, 'look');
});

test('materialize degrades an attachment the model cannot read', async () => {
  // The provider-switch case: an image attached under Anthropic, replayed to a
  // text-only model. Without this it would be sent as-is and 400.
  const dir = await tmpdir();
  const staged = await stage(dir, 'shot.png', PNG);
  assert.ok('attachment' in staged);

  const msgs: ChatMessage[] = [{ role: 'user', content: 'look', attachments: [staged.attachment] }];
  const out = await materialize(msgs, { dir, accepts: ['text'] });

  assert.equal(out.messages[0].attachments, undefined);
  assert.match(out.messages[0].content, /\[image: shot\.png — not supported/);
  assert.equal(out.warnings.length, 1);
});

test('materialize degrades binaries when redaction narrows accepts to text', async () => {
  // The privacy case: an image attached while redact was off is still in the history.
  // Replaying it on a redacted turn must not ship unscrubbable bytes under the
  // "Redacted before sending" banner, so redact-on narrows accepts to text.
  const dir = await tmpdir();
  const img = await stage(dir, 'shot.png', PNG);
  const txt = await stage(dir, 'notes.md', Buffer.from('call me at bob@example.com'));
  assert.ok('attachment' in img && 'attachment' in txt);

  const msgs: ChatMessage[] = [
    { role: 'user', content: 'look', attachments: [img.attachment, txt.attachment] },
  ];
  const out = await materialize(msgs, {
    dir,
    accepts: ['text'],
    reason: "redaction can't scrub it",
  });

  assert.equal(out.messages[0].attachments, undefined, 'no bytes reach the provider');
  assert.match(out.messages[0].content, /redaction can't scrub it/);
  assert.match(out.messages[0].content, /bob@example\.com/, 'text is still inlined, to be redacted');
  assert.equal(out.warnings.length, 1);
});

test('materialize survives a missing staged file', async () => {
  const dir = await tmpdir();
  const msgs: ChatMessage[] = [
    {
      role: 'user',
      content: 'x',
      attachments: [{ id: 'gone', name: 'gone.txt', kind: 'text', mime: 'text/plain', size: 1 }],
    },
  ];
  const out = await materialize(msgs, { dir, accepts: ['text'] });
  assert.match(out.messages[0].content, /attachment file is missing/);
});

test('materialize leaves plain turns untouched', async () => {
  const dir = await tmpdir();
  const msgs: ChatMessage[] = [
    { role: 'user', content: 'hi' },
    { role: 'assistant', content: 'hello' },
  ];
  const out = await materialize(msgs, { dir, accepts: ['image', 'text'] });
  assert.deepEqual(out.messages, msgs);
});

test('totalBytes sums the transcript', () => {
  const msgs: ChatMessage[] = [
    {
      role: 'user',
      content: '',
      attachments: [
        { id: 'a', name: 'a.png', kind: 'image', mime: 'image/png', size: 10 },
        { id: 'b', name: 'b.png', kind: 'image', mime: 'image/png', size: 5 },
      ],
    },
    { role: 'assistant', content: 'ok' },
  ];
  assert.equal(totalBytes(msgs), 15);
});
