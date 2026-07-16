import assert from 'node:assert/strict';
import test from 'node:test';
import {
  effortsForModel,
  parseClaudeEfforts,
  parseCodexEfforts,
} from './modelSelection';
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

test('Claude effort discovery follows the installed CLI help', () => {
  const parsed = parseClaudeEfforts(`
    --effort <level>      Effort level for the current session
                          (low, medium, high, xhigh, max)
    --model <model>       Model to use
  `);
  assert.deepEqual(parsed, {
    efforts: ['default', 'low', 'medium', 'high', 'xhigh', 'max'],
    byModel: {},
  });
});

test('Codex effort discovery stays model-specific', () => {
  const parsed = parseCodexEfforts(JSON.stringify({
    models: [
      { slug: 'sol', supported_reasoning_levels: [
        { effort: 'low' }, { effort: 'high' }, { effort: 'ultra' },
      ] },
      { slug: 'mini', supported_reasoning_levels: [
        { effort: 'minimal' }, { effort: 'low' }, { effort: 'high' },
      ] },
    ],
  }));
  assert.deepEqual(parsed, {
    efforts: ['default', 'low', 'high', 'ultra', 'minimal'],
    byModel: {
      sol: ['default', 'low', 'high', 'ultra'],
      mini: ['default', 'minimal', 'low', 'high'],
    },
  });
});

test('effort discovery rejects output without a capability list', () => {
  assert.equal(parseClaudeEfforts('Usage: claude'), undefined);
  assert.equal(parseCodexEfforts('{"models":[]}'), undefined);
  assert.equal(parseCodexEfforts('not json'), undefined);
});
