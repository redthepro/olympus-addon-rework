// Execute the shipped dashboard, with deferred network/clipboard operations.
// This verifies async behavior and DOM content; it is not visual browser QA.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static', 'app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor() {
    this.children = [];
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.hidden = false;
    this.listeners = {};
    this.selected = false;
    this.focused = false;
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  replaceChildren(...children) { this.children = children; }
  append(child) { this.children.push(child); }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  text() { return this.textContent + this.children.map(child => child.text()).join(' '); }
}

function runtime(clipboard) {
  let now = 1800000000000;
  let timerId = 0;
  const timers = new Map();
  const intervals = [];
  const nodes = new Map();
  const requests = [];
  const document = {
    visibilityState: 'visible', listeners: {},
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement() { return new Element(); },
    addEventListener(name, callback) { this.listeners[name] = callback; }
  };
  document.getElementById('prepare').disabled = true;
  document.getElementById('copy').disabled = true;
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const context = {
    document, navigator: {clipboard}, Date: Clock, AbortController,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, at: now + delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(callback) { intervals.push(callback); return intervals.length; },
    fetch(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      options.signal.addEventListener('abort', () => reject(new Error('Aborted')));
      requests.push({url, options, reject,
        reply(body, headers = {}, ok = true) {
          resolve({ok, json: async () => body, text: async () => body,
            headers: {get: name => Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ?? null}});
        }});
      return promise;
    }
  };
  vm.runInNewContext(source, context, {filename: 'app.js'});
  return {
    nodes, requests, document,
    node: id => document.getElementById(id),
    now: () => now / 1000,
    click(id) { const node = document.getElementById(id); return node.disabled ? undefined : node.listeners.click(); },
    interval() { intervals.forEach(callback => callback()); },
    tick(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now && timers.has(id)) { timers.delete(id); timer.callback(); }
      }
    }
  };
}

function census(rt, revision = 'a', count = 10) {
  return {snapshot_revision: revision.repeat(64), status: 'ok', sync_configured: true,
    credentials_configured: true, target_count: 1, worker_state: 'waiting', attempts: [],
    guilds: [{target: 'us/profile-forever-us/normal/olympus', characters: count,
      fetched_at: rt.now() - 5, source_modified_at: rt.now() - 45,
      scope_kind: 'ruleset', ruleset: {id: 123, name: 'Normal'}, experimental: true}]};
}

function exportReply(request, data, expires) {
  const row = data.guilds[0];
  const text = `OLYC1;us;profile-forever-us;normal;olympus;${Math.floor(row.fetched_at)};${Math.floor(row.source_modified_at)};${row.characters}`;
  request.reply(text, {'X-Olympus-Snapshot-Revision': data.snapshot_revision,
    'X-Olympus-Expires-At': String(expires ?? row.fetched_at + 86400)});
  return text;
}

async function show(rt, data) {
  assert.equal(rt.requests.length, 1);
  rt.requests[0].reply(data);
  await flush();
  assert.equal(rt.node('prepare').disabled, false);
}

async function prepare(rt, data, expires) {
  const action = rt.click('prepare');
  assert.equal(rt.requests.at(-1).url, '/v1/census');
  rt.requests.at(-1).reply(data);
  await flush();
  assert.equal(rt.requests.at(-1).url, '/v1/census/export');
  const text = exportReply(rt.requests.at(-1), data, expires);
  await action;
  assert.equal(rt.node('snapshot').value, text);
  return text;
}

test('startup and overlapping refresh triggers coalesce into one read', async () => {
  const rt = runtime();
  assert.equal(rt.requests[0].url, '/v1/census');
  rt.interval();
  rt.document.listeners.visibilitychange();
  assert.equal(rt.requests.length, 1);
  const data = census(rt);
  await show(rt, data);
  const action = rt.click('refresh');
  rt.interval();
  rt.document.listeners.visibilitychange();
  assert.equal(rt.requests.length, 2);
  rt.requests[1].reply(census(rt, 'b', 12));
  await action;
  assert.equal(rt.node('guilds').children[0].children[1].textContent, 12);
});

test('failed refresh retains last good rows, updates ages, and removes prepared text', async () => {
  const rt = runtime();
  const data = census(rt);
  await show(rt, data);
  await prepare(rt, data);
  rt.tick(7200000);
  const action = rt.click('refresh');
  rt.requests.at(-1).reject(new Error('Offline'));
  await action;
  assert.equal(rt.node('guilds').children.length, 1);
  assert.equal(rt.node('guilds').children[0].children[1].textContent, 10);
  assert.match(rt.node('guilds').text(), /2 hours ago/);
  assert.match(rt.node('guilds').text(), /Source is stale/);
  assert.match(rt.node('refreshed').textContent, /last successful view/);
  assert.equal(rt.node('snapshot').value, '');
  assert.equal(rt.node('copy').disabled, true);
});

test('new census revision invalidates an existing prepared snapshot', async () => {
  const rt = runtime();
  const data = census(rt);
  await show(rt, data);
  await prepare(rt, data);
  const action = rt.click('refresh');
  rt.requests.at(-1).reply(census(rt, 'b', 11));
  await action;
  assert.equal(rt.node('snapshot').value, '');
  assert.equal(rt.node('copy').disabled, true);
  assert.match(rt.node('export-status').textContent, /newer roster snapshot/);
});

test('snapshot expires without a network refresh and cannot be copied', async () => {
  let writes = 0;
  const rt = runtime({writeText: async () => { writes++; }});
  const data = census(rt);
  data.guilds[0].fetched_at = rt.now() - 86398;
  data.guilds[0].source_modified_at = data.guilds[0].fetched_at - 1;
  await show(rt, data);
  await prepare(rt, data);
  rt.tick(2001);
  assert.equal(rt.node('snapshot').value, '');
  assert.equal(rt.node('copy').disabled, true);
  rt.click('copy');
  assert.equal(writes, 0);
});

test('late export cannot repopulate text after a failed refresh', async () => {
  const rt = runtime();
  const data = census(rt);
  await show(rt, data);
  const action = rt.click('prepare');
  rt.requests.at(-1).reply(data);
  await flush();
  const oldExport = rt.requests.at(-1);
  assert.equal(oldExport.url, '/v1/census/export');
  rt.interval();
  rt.requests.at(-1).reject(new Error('Offline'));
  await flush();
  exportReply(oldExport, data);
  await action;
  assert.equal(rt.node('snapshot').value, '');
  assert.equal(rt.node('copy').disabled, true);
  assert.match(rt.node('connection').textContent, /Cannot refresh/);
});

test('export from a different revision is withheld while the view catches up', async () => {
  const rt = runtime();
  const data = census(rt);
  await show(rt, data);
  const action = rt.click('prepare');
  rt.requests.at(-1).reply(data);
  await flush();
  const next = census(rt, 'b', 12);
  exportReply(rt.requests.at(-1), next);
  await flush();
  assert.equal(rt.node('snapshot').value, '');
  assert.equal(rt.requests.at(-1).url, '/v1/census');
  rt.requests.at(-1).reply(next);
  await action;
  assert.equal(rt.node('copy').disabled, true);
  await prepare(rt, next);
});

test('copy uses prepared text and reports success only after clipboard completion', async () => {
  let copied, finish;
  const rt = runtime({writeText: text => { copied = text; return new Promise(resolve => { finish = resolve; }); }});
  const data = census(rt);
  await show(rt, data);
  const text = await prepare(rt, data);
  const action = rt.click('copy');
  assert.equal(copied, text);
  assert.doesNotMatch(rt.node('export-status').textContent, /^Copied/);
  finish();
  await action;
  assert.match(rt.node('export-status').textContent, /^Copied\./);
});

test('clipboard refusal keeps text selected and gives manual copy instructions', async () => {
  const rt = runtime({writeText: async () => { throw new Error('NotAllowedError'); }});
  const data = census(rt);
  await show(rt, data);
  const text = await prepare(rt, data);
  await rt.click('copy');
  assert.equal(rt.node('snapshot').value, text);
  assert.equal(rt.node('snapshot').selected, true);
  assert.equal(rt.node('snapshot').focused, true);
  assert.match(rt.node('export-status').textContent, /Ctrl\+C/);
  assert.doesNotMatch(rt.node('export-status').textContent, /^Copied/);
});

test('clipboard completion after invalidation warns instead of claiming current text', async () => {
  let finish;
  const rt = runtime({writeText: () => new Promise(resolve => { finish = resolve; })});
  const data = census(rt);
  await show(rt, data);
  await prepare(rt, data);
  const copying = rt.click('copy');
  rt.interval();
  rt.requests.at(-1).reply(census(rt, 'b', 15));
  await flush();
  finish();
  await copying;
  assert.equal(rt.node('snapshot').value, '');
  assert.match(rt.node('export-status').textContent, /changed or expired while copying/);
});

test('request timeout allows a later refresh to recover', async () => {
  const rt = runtime();
  rt.tick(10001);
  await flush();
  assert.equal(rt.requests[0].options.signal.aborted, true);
  assert.equal(rt.node('refresh').disabled, false);
  assert.match(rt.node('connection').textContent, /Cannot refresh/);
  const action = rt.click('refresh');
  rt.requests.at(-1).reply(census(rt));
  await action;
  assert.equal(rt.node('prepare').disabled, false);
});

test('malformed response preserves prior display and disables export', async () => {
  const rt = runtime();
  await show(rt, census(rt));
  const action = rt.click('refresh');
  rt.requests.at(-1).reply({guilds: [], attempts: []});
  await action;
  assert.equal(rt.node('guilds').children.length, 1);
  assert.equal(rt.node('prepare').disabled, true);
});
