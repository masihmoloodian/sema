import assert from 'node:assert/strict';
import test from 'node:test';
import { buildSystem, SEMA_WORKFLOW } from '../semaWorkflow';
import { PROVIDERS } from './index';

test('all eight providers receive the shared sema workflow', () => {
  assert.deepEqual(
    PROVIDERS.map((provider) => provider.id),
    [
      'claude-code',
      'codex',
      'opencode',
      'anthropic',
      'openai',
      'deepseek',
      'openrouter',
      'together',
    ],
  );
  for (const provider of PROVIDERS) {
    const prompt = buildSystem('', provider.readsWorkspace, 'agent');
    assert.ok(prompt.includes(SEMA_WORKFLOW), `${provider.id} missed the sema workflow`);
  }
});
