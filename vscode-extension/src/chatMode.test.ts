import assert from 'node:assert/strict';
import test from 'node:test';
import { compatibleCliSession, normalizeChatMode, shouldPrefetchIndex } from './chatMode';

const state = {
  cliSessionId: 'native-1',
  cliSessionProvider: 'codex',
  cliSessionModel: 'gpt-5.5',
  cliSessionMode: 'agent',
};

test('CLI resume requires the same provider, model, and mode', () => {
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'agent'), 'native-1');
  assert.equal(compatibleCliSession(state, 'claude-code', 'gpt-5.5', 'agent'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.4', 'agent'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'plan'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'ask'), undefined);
});

test('legacy sessions without model/mode restart safely with full transcript', () => {
  assert.equal(
    compatibleCliSession(
      { cliSessionId: 'old', cliSessionProvider: 'codex' },
      'codex',
      'gpt-5.5',
      'agent',
    ),
    undefined,
  );
});

test('unknown mode values fall back to Ask', () => {
  assert.equal(normalizeChatMode('agent'), 'agent');
  assert.equal(normalizeChatMode('plan'), 'plan');
  assert.equal(normalizeChatMode('something-else'), 'ask');
  assert.equal(normalizeChatMode(undefined), 'ask');
});

test('Plan and Agent always prefetch sema; Ask is opt-in', () => {
  assert.equal(shouldPrefetchIndex(false, 'agent'), true);
  assert.equal(shouldPrefetchIndex(false, 'plan'), true);
  assert.equal(shouldPrefetchIndex(false, 'ask'), false);
  assert.equal(shouldPrefetchIndex(true, 'ask'), true);
});
