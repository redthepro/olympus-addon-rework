'use strict';
const $ = id => document.getElementById(id);
const date = n => n ? new Date(n * 1000).toLocaleString() : 'Unknown';
const collectionErrors = {
  RulesetMismatch: 'Guild API did not confirm Normal ruleset; previous snapshot retained.',
  'HTTPError:401': 'Blizzard authentication failed. Check developer credentials.',
  'HTTPError:403': 'Blizzard denied access. Check credentials, region and API availability.',
  'HTTPError:404': 'Blizzard returned no roster for this target. This does not mean the guild is empty.',
  'HTTPError:429': 'Blizzard rate limit reached. Waiting before retrying.'
};
let loading = null;
let available = false;
let hasRows = false;
let currentRevision = '';
let lastViewedAt = 0;
let lastGoodData = null;
let preparing = false;
let copying = false;
let prepared = null;
let exportGeneration = 0;
let expiryTimer = null;

function updateButtons() {
  $('refresh').disabled = Boolean(loading);
  $('prepare').disabled = !available || !hasRows || Boolean(loading) || preparing || copying;
  $('copy').disabled = !prepared || copying;
}

function discardPrepared(message) {
  exportGeneration += 1;
  prepared = null;
  clearTimeout(expiryTimer);
  expiryTimer = null;
  $('snapshot').value = '';
  $('export-status').textContent = message;
  updateButtons();
}

function isCurrent(candidate) {
  return candidate && available && candidate === prepared && candidate.revision === currentRevision
    && candidate.expires > Date.now() / 1000;
}

function checkPrepared() {
  if (prepared && !isCurrent(prepared)) {
    discardPrepared('The prepared snapshot expired or changed. Prepare a new snapshot before importing.');
  }
}

async function read(path, kind) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error('Request failed');
    const body = kind === 'text' ? await response.text() : await response.json();
    return {body, headers: response.headers};
  } finally {
    clearTimeout(timeout);
  }
}

function datedAge(value) {
  if (!value) return 'Unknown';
  const seconds = Date.now() / 1000 - value;
  const age = seconds < -60 ? 'clock ahead' : seconds < 60 ? 'under a minute ago'
    : seconds < 3600 ? `${Math.floor(seconds / 60)} minutes ago`
    : `${Math.floor(seconds / 3600)} hours ago`;
  return `${date(value)} (${age})`;
}

function connectionText(data) {
  if (!data.sync_configured) {
    const missing = [];
    if (!data.credentials_configured) missing.push('Blizzard developer credentials');
    if (!data.target_count) missing.push('target guilds');
    return `Companion running. Configure ${missing.join(' and ') || 'the collector'} with the local setup script.`;
  }
  if (data.status === 'degraded') return 'The companion is reachable, but collection needs attention. Review the status below or restart the companion.';
  if (data.worker_state === 'polling') return 'Collecting Blizzard guild rosters. Existing snapshots remain available below.';
  if (data.next_poll_at) return `Companion running. Next collection attempt: ${date(data.next_poll_at)}.`;
  return 'Companion running. Review the collection results below.';
}

function render(data) {
  if (!Array.isArray(data.guilds) || !Array.isArray(data.attempts)
      || typeof data.snapshot_revision !== 'string' || !/^[a-f0-9]{64}$/.test(data.snapshot_revision)) {
    throw new Error('Invalid census response');
  }
  const experimental = row => Boolean(row.experimental) || /^[a-z]{2}\/profile-forever-[a-z]{2}\//.test(row.target);
  // Build the complete replacement first; a bad response cannot erase a good view.
  const guildRows = data.guilds.map(row => {
    const tr = document.createElement('tr');
    const state = !row.source_modified_at ? 'Source age unknown' : Date.now() / 1000 - row.source_modified_at > 1800 ? 'Source is stale' : 'Recent source snapshot';
    const fetchStale = Date.now() / 1000 - row.fetched_at > 1800;
    let scope = '';
    if (experimental(row)) {
      const normal = row.scope_kind === 'ruleset' && row.ruleset && Number.isInteger(row.ruleset.id)
        && row.ruleset.id > 0 && typeof row.ruleset.name === 'string' && ['normal', 'pvp', 'roleplay', 'roleplay pvp'].includes(row.ruleset.name.toLowerCase());
      scope = normal ? '; ' + row.ruleset.name + ' (API-reported)' : '; ruleset unverified';
    }
    for (const value of [row.target, row.characters, datedAge(row.fetched_at), datedAge(row.source_modified_at),
      state + (fetchStale ? '; fetch overdue' : '') + scope + (experimental(row) ? '; experimental Forever' : '')]) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    return tr;
  });
  const attempts = data.attempts.map(item => {
    const result = item.error ? (collectionErrors[item.error] || `Collection failed (${item.error}).`) : 'Last request succeeded';
    const retry = item.error && item.next_retry_at ? ` Next attempt no earlier than ${date(item.next_retry_at)}.` : '';
    const li = document.createElement('li');
    li.textContent = `${item.target}: ${result} (${date(item.attempted_at)})${retry}`;
    return li;
  });
  $('guilds').replaceChildren(...guildRows);
  $('attempts').replaceChildren(...attempts);
  $('experimental').hidden = !data.guilds.some(experimental);
  $('connection').textContent = connectionText(data);
  $('summary').textContent = guildRows.length ? `${guildRows.length} guild snapshots. The view refreshes every 30 seconds; Blizzard collection follows its separate schedule.` : 'No roster snapshots collected yet.';
  hasRows = guildRows.length > 0;
}

function load() {
  if (loading) return loading; // Coalesce interval, manual and prepare reads.
  loading = (async () => {
    try {
      const {body: data} = await read('/v1/census', 'json');
      render(data);
      lastGoodData = data;
      if (currentRevision && currentRevision !== data.snapshot_revision) {
        discardPrepared('A newer roster snapshot is available. Prepare a new snapshot before importing.');
      }
      currentRevision = data.snapshot_revision;
      available = true;
      lastViewedAt = Date.now() / 1000;
      $('refreshed').textContent = `View refreshed ${date(lastViewedAt)}. Roster fetch and source dates are shown separately below.`;
      checkPrepared();
      return true;
    } catch (error) {
      available = false;
      discardPrepared('The companion could not be refreshed. Refresh the view before preparing another snapshot.');
      if (lastGoodData) render(lastGoodData); // Recompute displayed ages without inventing a new fetch.
      $('connection').textContent = 'Cannot refresh the local companion. Check that it is running, then press Refresh view.';
      $('refreshed').textContent = lastViewedAt ? `Showing the last successful view from ${date(lastViewedAt)}. Current collection status is unknown.` : 'No view loaded yet.';
      if (!lastViewedAt) $('summary').textContent = 'No roster view available. Start the companion, then press Refresh view.';
      return false;
    } finally {
      loading = null;
      updateButtons();
    }
  })();
  updateButtons();
  return loading;
}

async function prepare() {
  if (preparing || copying) return;
  preparing = true;
  discardPrepared('Refreshing the view before preparing a snapshot…');
  let ticket = null;
  try {
    if (!await load()) return;
    ticket = exportGeneration;
    const expectedRevision = currentRevision;
    $('export-status').textContent = 'Preparing snapshot…';
    const {body: text, headers} = await read('/v1/census/export', 'text');
    if (ticket !== exportGeneration || !available) return;
    const revision = headers.get('X-Olympus-Snapshot-Revision');
    const expires = Number(headers.get('X-Olympus-Expires-At'));
    if (revision !== expectedRevision || currentRevision !== expectedRevision) {
      discardPrepared('The census changed while preparing. Prepare the snapshot again.');
      await load();
      return;
    }
    if (!text) {
      discardPrepared('No eligible snapshots. Collect a roster first; snapshots fetched over 24 hours ago are excluded.');
      return;
    }
    if (!Number.isFinite(expires) || expires <= Date.now() / 1000 || text.length > 49152) {
      throw new Error('Invalid or expired export');
    }
    prepared = {text, revision, expires};
    $('snapshot').value = text;
    $('export-status').textContent = 'Snapshot prepared. Press Copy snapshot, then paste into /oly censusapi paste.';
    expiryTimer = setTimeout(checkPrepared, Math.max(1, expires * 1000 - Date.now() + 1));
  } catch (error) {
    if (ticket === null || ticket === exportGeneration) {
      discardPrepared('Cannot prepare snapshot. Check collection status, refresh the view, and try again.');
    }
  } finally {
    preparing = false;
    updateButtons();
  }
}

async function copySnapshot() {
  checkPrepared();
  const candidate = prepared;
  if (!isCurrent(candidate) || copying) return;
  copying = true;
  updateButtons();
  try {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') throw new Error('Clipboard unavailable');
    // This call occurs directly in the user's click; no network await loses activation.
    await navigator.clipboard.writeText(candidate.text);
    if (isCurrent(candidate)) {
      $('export-status').textContent = 'Copied. In WoW, open /oly censusapi paste, paste, and import.';
    } else {
      $('export-status').textContent = 'The snapshot changed or expired while copying. Prepare and copy again before importing.';
    }
  } catch (error) {
    if (isCurrent(candidate)) {
      $('snapshot').focus();
      $('snapshot').select();
      $('export-status').textContent = 'Clipboard access was unavailable. Text is selected; press Ctrl+C (or Command+C) to copy it manually.';
    }
  } finally {
    copying = false;
    updateButtons();
  }
}

$('refresh').addEventListener('click', load);
$('prepare').addEventListener('click', prepare);
$('copy').addEventListener('click', copySnapshot);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') { checkPrepared(); void load(); }
});
void load();
setInterval(() => { checkPrepared(); void load(); }, 30000);
