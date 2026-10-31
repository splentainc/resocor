'use strict';
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await res.json();
  if (!res.ok || result.error) throw new Error(result.error || `Request failed (${res.status})`);
  return result;
}
function setBusy(value) {
  busy = value;
  ['send','reset','policy','conversation-select'].forEach(id => $(id).disabled = value);
  document.querySelectorAll('input[name="detail"], [data-render]').forEach(el => el.disabled = value);
}
function showSessionState(state) {
  const c = state?.context;
  $('session-state').textContent = c ? `Context: ${c.identity.entity} · ${c.identity.metric} · FY${c.identity.period}` : 'No active factual context';
}
async function refreshConversations() {
  const sessions = await api('/api/conversations');
  $('conversation-select').innerHTML = sessions.map(s => `<option value="${esc(s.id)}">${esc(s.title)} · ${s.turn_count} turn(s)</option>`).join('');
  $('conversation-select').value = sessionId;
}
function clearView() {
  turns.length = 0; journalTurns.length = 0; selected = null; pendingRequest = null;
  $('messages').innerHTML = initialWelcome; renderExamples();
  $('inspection-query').textContent = 'Select a response to inspect its execution.';
  $('inspection-meta').textContent = ''; $('trace-panel').innerHTML = initialPanel;
  $('turn-count').textContent = '0 turns'; $('export').disabled = true;
  $('question').value = ''; $('request-status').textContent = ''; showTab('steps');
}
async function loadConversation(id, turnId) {
  setBusy(true);
  try {
    const session = await api(`/api/conversation?id=${encodeURIComponent(id)}`);
    clearView(); sessionId = session.id;
    sessionStorage.setItem('resocor.conversation',sessionId);
    for (const turn of session.turns) { journalTurns.push(turn); addTurn(turn); }
    if (turnId) {
      const chosen = session.turns.find(t => t.id === turnId);
      const rootId = chosen?.render_of || turnId;
      const index = turns.findIndex(t => (t.render_of || t.id) === rootId);
      if (index >= 0) {
        if (chosen) { turns[index] = chosen; paintTurn(chosen,index); }
        inspect(index);
      }
    }
    showSessionState(session.state);
    const lastQuery = [...session.turns].reverse().find(t => t.kind !== 'render');
    if (lastQuery) {
      const option = document.querySelector(`input[name="detail"][value="${['crisp','detailed','audit'].includes(lastQuery.detail) ? lastQuery.detail : 'crisp'}"]`);
      option.checked = true;
      $('policy').value = lastQuery.policy || 'none';
    }
    await refreshConversations();
    if (!session.integrity_ok) $('request-status').textContent = 'The saved trace hash chain did not verify. Inspect the query journal before relying on these records.';
  } catch (error) {
    $('request-status').textContent = error.message;
    throw error;
  } finally { setBusy(false); }
}
async function newConversation() {
  setBusy(true);
  try { const s = await api('/api/conversations',{}); await loadConversation(s.id); }
  catch(error) { $('request-status').textContent = error.message; }
  finally { setBusy(false); }
}
function contextView(r) {
  return `<h3 class="subheading">Conversation & query links</h3>${json({conversation_id:r.conversation_id,turn_id:r.id,parent_turn_id:r.parent_turn_id,render_of:r.render_of || null})}` +
    `<h3 class="subheading">Context resolution</h3>${json(r.context || {})}` +
    `<h3 class="subheading">State before → after</h3>${raw('State before this request',r.state_before)}${raw('State after this request',r.state_after)}` +
    `<h3 class="subheading">Saved trace identity</h3>${json(r.audit || {})}` +
    `<p class="panel-intro">The source manifest identifies the loaded runtime files and fact snapshots. Hash links detect accidental changes to saved records; this local journal is not a signed or immutable archive.</p>`;
}
let logEntries = [], nextLogOffset = null;
async function loadLogs(offset=0) {
  if (offset === 0) { logEntries = []; $('trace-panel').innerHTML = '<p class="panel-intro">Loading saved query log…</p>'; }
  try {
    const log = await api(`/api/logs?offset=${offset}`);
    logEntries.push(...log.entries); nextLogOffset = log.next_offset;
    if (activeTab !== 'logs') return;
    $('trace-panel').innerHTML = `<p class="panel-intro">${log.total} saved query/render request(s), including refusals and interrupted requests. Select a record to reopen its full trace.</p>` +
      `<div class="table-scroll"><table><thead><tr><th>Query / conversation</th><th>Detail</th><th>Outcome</th><th>Time</th></tr></thead><tbody>` +
      logEntries.map(r => `<tr><td><button class="log-link" data-log="${esc(r.id)}" data-session="${esc(r.conversation_id)}">${esc(r.query)}</button><div class="trace-id">${esc(r.id)}<br>${esc(r.at)} · ${esc(r.kind)}<br>Session ${esc(r.conversation_id)}<br>Hash ${esc(r.record_hash || 'incomplete')}</div></td><td>${esc(r.detail)}</td><td>${badge(r.outcome)}</td><td>${fmt(r.total_us)}</td></tr>`).join('') +
      `</tbody></table></div>` + (nextLogOffset !== null ? '<button class="quiet" id="more-logs">Load older requests</button>' : '');
  } catch(error) { $('trace-panel').innerHTML = empty(error.message); }
}
async function renderAnswer(index, detail) {
  if (busy || turns[index].detail === detail) return;
  setBusy(true);
  $('request-status').textContent = `Rendering the saved receipt as ${detail}…`;
  try {
    const r = await api('/api/render',{turn_id:turns[index].render_of || turns[index].id,detail,request_id:crypto.randomUUID()});
    journalTurns.push(r); addTurn(r);
    $('request-status').textContent = `${detail[0].toUpperCase()+detail.slice(1)} response saved as a linked render. Conversation context is unchanged.`;
  } catch(error) { $('request-status').textContent = error.message; }
  finally { setBusy(false); }
}
$('conversation-select').addEventListener('change',() => { loadConversation($('conversation-select').value).catch(() => {}); });
$('trace-panel').addEventListener('click',event => {
  const entry = event.target.closest('[data-log]');
  if (entry && !busy) loadConversation(entry.dataset.session,entry.dataset.log).catch(() => {});
  if (event.target.closest('#more-logs') && nextLogOffset !== null) loadLogs(nextLogOffset);
});
(async () => {
  setBusy(true);
  try {
    const status = await api('/api/status'); examples = status.examples;
    $('connection').textContent = '● SQLite journal ready';
    const saved = sessionStorage.getItem('resocor.conversation');
    if (saved) {
      const sessions = await api('/api/conversations');
      if (sessions.some(s => s.id === saved)) { await loadConversation(saved); return; }
    }
    await newConversation();
  } catch(error) { $('request-status').textContent = `Cannot restore the conversation: ${error.message}. Reload to retry.`; }
  finally { setBusy(!sessionId); }
})();
