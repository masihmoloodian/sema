import assert from 'node:assert/strict';
import test from 'node:test';
import { effortsForModel } from './modelSelection';
import { CodexProvider } from './providers/cli';

test('Codex reasoning choices follow the selected model', () => {
  const provider = new CodexProvider();
  assert.deepEqual(effortsForModel(provider, 'gpt-5.6-sol'), [
    'default', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra',
  ]);
  assert.deepEqual(effortsForModel(provider, 'gpt-5.6-luna'), [
    'default', 'low', 'medium', 'high', 'xhigh', 'max',
  ]);
  assert.deepEqual(effortsForModel(provider, 'gpt-5.5'), [
    'default', 'low', 'medium', 'high', 'xhigh',
  ]);
});
