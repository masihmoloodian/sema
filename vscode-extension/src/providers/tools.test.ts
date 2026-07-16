import assert from 'node:assert/strict';
import test from 'node:test';
import { toolsForMode } from './tools';

function names(readOnly: boolean, semaEnabled: boolean): string[] {
  return toolsForMode(readOnly, semaEnabled).map((tool) =>
    tool.type === 'function' ? tool.function.name : tool.type,
  );
}

test('index toggle removes sema tools but preserves normal workspace tools', () => {
  const off = names(false, false);
  assert.ok(!off.includes('search_code'));
  assert.ok(!off.includes('get_code'));
  assert.ok(!off.includes('check_reuse'));
  assert.ok(off.includes('grep'));
  assert.ok(off.includes('read_file'));
  assert.ok(off.includes('write_file'));
  assert.ok(off.includes('run_command'));

  const on = names(false, true);
  assert.ok(on.includes('search_code'));
  assert.ok(on.includes('get_code'));
  assert.ok(on.includes('check_reuse'));
});
