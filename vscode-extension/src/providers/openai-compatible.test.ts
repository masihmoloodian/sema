import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { OpenAICompatibleProvider } from './openai-compatible';
import { StreamOptions } from './types';

interface RequestBody {
  messages: Array<{ role: string; content?: string }>;
  tools?: Array<{ function: { name: string } }>;
}

interface ScriptCall { name: string; args: Record<string, unknown> }

async function scriptedAgent(calls: ScriptCall[]): Promise<{
  baseURL: string;
  requests: RequestBody[];
  close: () => Promise<void>;
}> {
  const requests: RequestBody[] = [];
  let step = 0;
  const server = http.createServer((req, res) => {
    let raw = '';
    req.setEncoding('utf8');
    req.on('data', (part) => { raw += part; });
    req.on('end', () => {
      requests.push(JSON.parse(raw) as RequestBody);
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      const call = calls[step++];
      if (call) {
        sendChunk(res, {
          id: `step-${step}`, object: 'chat.completion.chunk', created: 1, model: 'mock/model',
          choices: [{ index: 0, finish_reason: 'tool_calls', delta: {
            tool_calls: [{ index: 0, id: `call-${step}`, type: 'function',
              function: { name: call.name, arguments: JSON.stringify(call.args) } }],
          } }],
        });
      } else {
        sendChunk(res, {
          id: 'done', object: 'chat.completion.chunk', created: 1, model: 'mock/model',
          choices: [{ index: 0, finish_reason: 'stop', delta: { content: 'implemented and tested' } }],
        });
      }
      res.end('data: [DONE]\n\n');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('scripted server did not bind');
  return {
    baseURL: `http://127.0.0.1:${address.port}/v1`, requests,
    close: () => new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve()),
    ),
  };
}

async function agentFixture(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-agent-edit-'));
  await fs.mkdir(path.join(root, 'src'), { recursive: true });
  await fs.mkdir(path.join(root, 'test'), { recursive: true });
  await fs.writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', scripts: { test: 'node --test' } }));
  await fs.writeFile(path.join(root, 'src', 'greeting.js'), 'export const greeting = "hello";\n');
  await fs.writeFile(path.join(root, 'src', 'discount.js'),
    'const RATES = { SAVE10: 0.1, SAVE25: 0.25 };\n\nexport function discountRateFor(code) {\n  return RATES[String(code ?? "").toUpperCase()] ?? 0;\n}\n');
  await fs.writeFile(path.join(root, 'src', 'checkout.js'),
    'export function checkoutTotal(items, shipping = 5) {\n  const subtotal = items.reduce(\n    (sum, item) => sum + item.price * item.quantity,\n    0,\n  );\n  return subtotal + shipping;\n}\n');
  await fs.writeFile(path.join(root, 'test', 'greeting.test.js'),
    'import assert from "node:assert/strict";\nimport test from "node:test";\nimport { greeting } from "../src/greeting.js";\n\ntest("exports the greeting", () => {\n  assert.equal(greeting, "hello");\n});\n');
  await fs.writeFile(path.join(root, 'test', 'checkout.test.js'),
    'import assert from "node:assert/strict";\nimport test from "node:test";\nimport { checkoutTotal } from "../src/checkout.js";\n\ntest("totals line items and shipping", () => {\n  assert.equal(checkoutTotal([{ price: 10, quantity: 2 }]), 25);\n});\n');
  return root;
}

async function fakeSema(root: string, indexed: boolean): Promise<string> {
  const file = path.join(root, 'fake-sema');
  const payload = indexed
    ? { results: [{ file: 'src/greeting.js', name: 'greeting', type: 'variable', signature: 'greeting', start_line: 1 }] }
    : { error: 'no_index', message: 'No index found.' };
  await fs.writeFile(file, `#!/bin/sh\nprintf '%s\\n' '${JSON.stringify(payload)}'\n`, { mode: 0o755 });
  return file;
}

function agentProvider(baseURL: string): OpenAICompatibleProvider {
  return new OpenAICompatibleProvider({
    id: 'openrouter', label: 'OpenRouter', baseURL,
    models: ['provider/model'], defaultModel: 'provider/model',
    secretKey: 'test', keyHint: 'test',
  });
}

function sendChunk(res: http.ServerResponse, body: object): void {
  res.write(`data: ${JSON.stringify(body)}\n\n`);
}

async function mockOpenRouter(): Promise<{
  baseURL: string;
  requests: RequestBody[];
  close: () => Promise<void>;
}> {
  const requests: RequestBody[] = [];
  const server = http.createServer((req, res) => {
    let raw = '';
    req.setEncoding('utf8');
    req.on('data', (part) => { raw += part; });
    req.on('end', () => {
      const body = JSON.parse(raw) as RequestBody;
      requests.push(body);
      res.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      });
      const lastRole = body.messages.at(-1)?.role;
      if (body.tools?.length && lastRole !== 'tool') {
        sendChunk(res, {
          id: 'chunk-1', object: 'chat.completion.chunk', created: 1, model: 'mock/model',
          choices: [{ index: 0, finish_reason: 'tool_calls', delta: {
            role: 'assistant',
            tool_calls: [{
              index: 0, id: 'call-1', type: 'function',
              function: { name: 'list_directory', arguments: '{"path":"."}' },
            }],
          } }],
        });
      } else {
        sendChunk(res, {
          id: 'chunk-2', object: 'chat.completion.chunk', created: 1, model: 'mock/model',
          choices: [{ index: 0, finish_reason: 'stop', delta: { content: 'done' } }],
        });
      }
      res.end('data: [DONE]\n\n');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('mock server did not bind');
  return {
    baseURL: `http://127.0.0.1:${address.port}/v1`,
    requests,
    close: () => new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve()),
    ),
  };
}

function options(cwd: string, mode: 'ask' | 'plan' | 'agent', output: string[], activity: string[]): StreamOptions {
  return {
    apiKey: 'test-key',
    cwd,
    model: 'provider/model',
    system: `mode: ${mode}`,
    messages: [{ role: 'user', content: 'inspect this project' }],
    maxTokens: 100,
    signal: new AbortController().signal,
    agent: mode === 'agent',
    plan: mode === 'plan',
    onDelta: (value) => output.push(value),
    onActivity: (tool, detail) => activity.push(`${tool}:${detail}`),
  };
}

test('OpenRouter-style bare models use sema mode logic end to end', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-openrouter-'));
  await fs.writeFile(path.join(root, 'example.ts'), 'export const answer = 42;\n');
  const mock = await mockOpenRouter();
  t.after(async () => {
    await mock.close();
    await fs.rm(root, { recursive: true, force: true });
  });
  const provider = new OpenAICompatibleProvider({
    id: 'openrouter', label: 'OpenRouter', baseURL: mock.baseURL,
    models: ['provider/model'], defaultModel: 'provider/model',
    secretKey: 'test', keyHint: 'test',
  });

  const askOutput: string[] = [];
  await provider.stream(options(root, 'ask', askOutput, []));
  assert.deepEqual(askOutput, ['done']);
  assert.equal(mock.requests[0].tools, undefined);

  const planOutput: string[] = [];
  const planActivity: string[] = [];
  const planOpts = options(root, 'plan', planOutput, planActivity);
  planOpts.semaBin = 'sema';
  await provider.stream(planOpts);
  const planTools = mock.requests[1].tools?.map((tool) => tool.function.name) ?? [];
  assert.ok(planTools.includes('search_code'));
  assert.ok(planTools.includes('check_reuse'));
  assert.ok(planTools.includes('read_file'));
  assert.ok(!planTools.includes('write_file'));
  assert.ok(!planTools.includes('run_command'));
  assert.deepEqual(planActivity, ['list_directory:.']);
  assert.deepEqual(planOutput, ['done']);

  const agentOutput: string[] = [];
  const agentActivity: string[] = [];
  const agentOpts = options(root, 'agent', agentOutput, agentActivity);
  agentOpts.semaBin = 'sema';
  await provider.stream(agentOpts);
  const agentTools = mock.requests[3].tools?.map((tool) => tool.function.name) ?? [];
  assert.ok(agentTools.includes('search_code'));
  assert.ok(agentTools.includes('check_reuse'));
  assert.ok(agentTools.includes('write_file'));
  assert.ok(agentTools.includes('run_command'));
  assert.deepEqual(agentActivity, ['list_directory:.']);
  assert.deepEqual(agentOutput, ['done']);
  assert.equal(mock.requests.length, 5);
});

test('indexed Agent performs a simple semantic edit and verifies it', async (t) => {
  const root = await agentFixture();
  const semaBin = await fakeSema(root, true);
  const mock = await scriptedAgent([
    { name: 'search_code', args: { query: 'exported greeting value' } },
    { name: 'edit_file', args: { path: 'src/greeting.js', old_string: '"hello"', new_string: '"hello sema"' } },
    { name: 'edit_file', args: { path: 'test/greeting.test.js', old_string: '"hello"', new_string: '"hello sema"' } },
    { name: 'run_command', args: { command: 'env -u NODE_TEST_CONTEXT npm test' } },
  ]);
  t.after(async () => { await mock.close(); await fs.rm(root, { recursive: true, force: true }); });
  const output: string[] = [], activity: string[] = [];
  const opts = options(root, 'agent', output, activity);
  opts.semaBin = semaBin;
  await agentProvider(mock.baseURL).stream(opts);
  assert.equal(await fs.readFile(path.join(root, 'src', 'greeting.js'), 'utf8'),
    'export const greeting = "hello sema";\n');
  assert.deepEqual(activity.map((item) => item.split(':')[0]),
    ['search_code', 'edit_file', 'edit_file', 'run_command']);
  assert.match(mock.requests[1].messages.at(-1)?.content ?? '', /src\/greeting.js/);
  assert.match(mock.requests[4].messages.at(-1)?.content ?? '', /pass 2/);
});

test('unindexed Agent falls back and completes a complex cross-module edit', async (t) => {
  const root = await agentFixture();
  const semaBin = await fakeSema(root, false);
  const updatedCheckout =
    'import { discountRateFor } from "./discount.js";\n\nexport function checkoutTotal(items, shipping = 5, couponCode) {\n  const subtotal = items.reduce(\n    (sum, item) => sum + item.price * item.quantity,\n    0,\n  );\n  const discounted = Math.max(0, subtotal * (1 - discountRateFor(couponCode)));\n  return discounted + shipping;\n}\n';
  const extraTests =
    '\n\ntest("applies an existing coupon before shipping", () => {\n  assert.equal(checkoutTotal([{ price: 20, quantity: 2 }], 5, "save25"), 35);\n});\n\ntest("unknown coupons leave the total unchanged", () => {\n  assert.equal(checkoutTotal([{ price: 10, quantity: 2 }], 5, "nope"), 25);\n});\n';
  const originalCheckout = await fs.readFile(path.join(root, 'src', 'checkout.js'), 'utf8');
  const originalTests = await fs.readFile(path.join(root, 'test', 'checkout.test.js'), 'utf8');
  const mock = await scriptedAgent([
    { name: 'search_code', args: { query: 'checkout coupon discount helper' } },
    { name: 'read_file', args: { path: 'src/discount.js' } },
    { name: 'read_file', args: { path: 'src/checkout.js' } },
    { name: 'edit_file', args: { path: 'src/checkout.js', old_string: originalCheckout, new_string: updatedCheckout } },
    { name: 'edit_file', args: { path: 'test/checkout.test.js', old_string: originalTests, new_string: originalTests.trimEnd() + extraTests } },
    { name: 'run_command', args: { command: 'env -u NODE_TEST_CONTEXT npm test' } },
  ]);
  t.after(async () => { await mock.close(); await fs.rm(root, { recursive: true, force: true }); });
  const opts = options(root, 'agent', [], []);
  opts.semaBin = semaBin;
  await agentProvider(mock.baseURL).stream(opts);
  const finalSource = await fs.readFile(path.join(root, 'src', 'checkout.js'), 'utf8');
  assert.match(finalSource, /import \{ discountRateFor \}/);
  assert.match(finalSource, /subtotal \* \(1 - discountRateFor\(couponCode\)\)/);
  assert.match(mock.requests[1].messages.at(-1)?.content ?? '', /No results|No index/i);
  assert.match(mock.requests[6].messages.at(-1)?.content ?? '', /pass 4/);
});
