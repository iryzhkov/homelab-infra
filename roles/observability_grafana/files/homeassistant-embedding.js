#!/usr/bin/env node
// Reconcile only the owned HA storage dashboard using official WebSocket APIs.
// Capability URLs are decrypted in memory and saved only in private HA app state.
const { execFileSync } = require('node:child_process');
const { isDeepStrictEqual } = require('node:util');
const path = 'homelab-observability';
const marker = 'homelab-observability-v1';
const args = process.argv.slice(2);
const check = args.includes('--check');
const at = args.indexOf('--shares-file');
if (at < 0 || !args[at + 1]) {
  console.log(JSON.stringify({ failed: true, reason: 'shares_file_required' }));
  process.exit(1);
}
function fail(reason) { throw new Error(reason); }
async function session(token) {
  const ws = new WebSocket('wss://home.ryzhkov.dev/api/websocket');
  let next = 0;
  const pending = new Map();
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('websocket_auth_timeout')), 15000);
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === 'auth_required') ws.send(JSON.stringify({ type: 'auth', access_token: token }));
      else if (message.type === 'auth_ok') { clearTimeout(timer); resolve(); }
      else if (message.type === 'auth_invalid') { clearTimeout(timer); reject(new Error('websocket_auth_failed')); }
      else if (pending.has(message.id)) {
        const item = pending.get(message.id);
        pending.delete(message.id);
        clearTimeout(item.timer);
        message.success ? item.resolve(message.result) : item.reject(new Error(message.error?.code || 'websocket_command_failed'));
      }
    };
    ws.onerror = () => reject(new Error('websocket_connection_failed'));
  });
  return {
    call(message) {
      if (check && !['lovelace/dashboards/list', 'lovelace/config'].includes(message.type)) fail('check_mode_mutation_refused');
      return new Promise((resolve, reject) => {
        const id = ++next;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('websocket_command_timeout')); }, 15000);
        pending.set(id, { resolve, reject, timer });
        ws.send(JSON.stringify({ ...message, id }));
      });
    },
    close() { ws.close(); },
  };
}
(async () => {
  const secrets = JSON.parse(execFileSync('sops', ['--decrypt', '--output-type', 'json', args[at + 1]], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
  const views = [];
  for (const [key, title, icon, viewPath] of [
    ['OVERVIEW', 'Overview', 'mdi:chart-box-outline', 'overview'],
    ['AGENTS', 'Agent capacity', 'mdi:server', 'agents'],
    ['HOMEASSISTANT', 'Home Assistant', 'mdi:home-analytics', 'homeassistant'],
  ]) {
    const url = secrets['GF_' + key + '_URL'];
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.hostname !== 'logs.ryzhkov.dev' || !/^\/public-dashboards\/[a-f0-9]{32}$/.test(parsed.pathname)) fail('invalid_capability_url');
    const response = await fetch(url, { redirect: 'error' });
    if (!response.ok) fail('shared_dashboard_not_ready');
    const frame = (response.headers.get('x-frame-options') || '').toLowerCase();
    if (frame.includes('deny') || frame.includes('sameorigin')) fail('grafana_embedding_not_ready');
    const csp = response.headers.get('content-security-policy') || '';
    const ancestor = csp.match(/frame-ancestors\s+([^;]+)/i);
    if (ancestor && !ancestor[1].includes('https://home.ryzhkov.dev') && !ancestor[1].includes('*')) fail('grafana_frame_ancestors_block_homeassistant');
    views.push({ title, path: viewPath, icon, type: 'panel', cards: [{ type: 'iframe', url, aspect_ratio: '100%' }] });
  }
  const desired = { title: 'Observability', homelab_observability_managed_by: marker, views };
  const admin = execFileSync('secret-tool', ['lookup', 'service', 'homeassistant'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  if (!admin) fail('homeassistant_admin_credential_missing');
  const api = await session(admin);
  try {
    const before = await api.call({ type: 'lovelace/dashboards/list' });
    const own = before.find((dashboard) => dashboard.url_path === path);
    let current;
    if (own) {
      if (own.mode !== 'storage') fail('existing_dashboard_is_not_owned_storage');
      try { current = await api.call({ type: 'lovelace/config', url_path: path }); }
      catch (error) { if (error.message !== 'config_not_found') throw error; }
      if (current && current.homelab_observability_managed_by !== marker) fail('existing_dashboard_owned_by_someone_else');
    }
    const changed = !own || !isDeepStrictEqual(current, desired);
    if (changed && !check) {
      if (!own) await api.call({ type: 'lovelace/dashboards/create', url_path: path, title: 'Observability', icon: 'mdi:chart-box-outline', show_in_sidebar: true, require_admin: false });
      await api.call({ type: 'lovelace/config/save', url_path: path, config: desired });
      const actual = await api.call({ type: 'lovelace/config', url_path: path });
      if (!isDeepStrictEqual(actual, desired)) fail('dashboard_readback_failed');
      const after = await api.call({ type: 'lovelace/dashboards/list' });
      for (const previous of before) {
        if (!after.some((dashboard) => isDeepStrictEqual(dashboard, previous))) fail('previous_dashboard_changed');
      }
    }
    console.log(JSON.stringify({ changed, check_mode: check, dashboard_url_path: path, views: 3, previous_dashboards_preserved: true }));
  } finally { api.close(); }
})().catch(() => {
  // Never print API bodies, URLs, exception messages or decrypted credentials.
  console.log(JSON.stringify({ failed: true, reason: 'embedding_reconciliation_failed_check_private_prerequisites' }));
  process.exit(1);
});
