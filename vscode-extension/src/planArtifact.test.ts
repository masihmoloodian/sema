import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { readPlanArtifact, savePlanArtifact } from './planArtifact';

test('Plan mode saves one durable Markdown artifact and Agent can read it', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-plan-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const artifact = await savePlanArtifact(
    root,
    '../unsafe/session',
    'Add authentication',
    '1. Inspect `src/auth.ts`.\n2. Add tests.',
  );
  assert.match(artifact.relativePath, /^\.sema\/plans\//);
  assert.ok(!artifact.relativePath.includes('..'));
  assert.equal(await readPlanArtifact(root, artifact.relativePath), artifact.markdown);
  assert.match(artifact.markdown, /^# Add authentication/m);
  assert.match(artifact.markdown, /Add tests/);

  const names = await fs.readdir(path.join(root, '.sema', 'plans'));
  assert.deepEqual(names, [path.basename(artifact.relativePath)]);
});

test('a later Plan turn replaces the session plan atomically', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-plan-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const first = await savePlanArtifact(root, 'chat-1', 'Plan', 'old plan');
  const second = await savePlanArtifact(root, 'chat-1', 'Plan', 'new plan');
  assert.equal(first.relativePath, second.relativePath);
  assert.doesNotMatch(await readPlanArtifact(root, second.relativePath), /old plan/);
  assert.match(await readPlanArtifact(root, second.relativePath), /new plan/);
});

test('plan reads cannot escape the workspace', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-plan-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  assert.equal(await readPlanArtifact(root, '../secret.md'), '');
});
