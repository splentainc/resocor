'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = value => `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
const raw = (label, value) => `<details class="raw"><summary>${esc(label)}</summary>${json(value)}</details>`;
const empty = text => `<div class="empty-note">${esc(text)}</div>`;
const fmt = us => us == null ? '—' : us >= 1000 ? `${(us / 1000).toFixed(2)} ms` : `${us.toFixed(1)} µs`;
const stamp = at => at ? new Date(at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
const utc = at => at ? at.split('T')[1].replace('+00:00', 'Z') : '—';
const badge = (text, type = text) => `<span class="badge ${esc(type)}">${esc(text)}</span>`;
const turns = [];
const journalTurns = [];
let sessionId = null, pendingRequest = null;
const getDetail = () => document.querySelector('input[name="detail"]:checked').value;
let selected = null, activeTab = 'steps', busy = false;
const titles = {context:'Resolve conversation context',segments:'Segment & compose', query:'Resolve checked query', facts:'Retrieve source facts', execution:'Execute checked operation', validation:'Validate facts & NLG input', dmn:'Evaluate DMN policy', nlg:'Generate & verify response', chatter:'Reviewed chatter lookup'};
const descriptions = {
  context:r => r.context?.status === 'resolved' ? `Inherited from turn ${r.context.context_from_turn}: ${r.resolved_query}` : r.context?.reason || 'Standalone complete question; no inherited slots.',
  segments:r => `${r.segments.length} typed segments · ${r.tree?.node ?? 'no query tree'}`,
  query:r => r.plan ? `${r.plan.op} · ${r.plan.cases.length} lookup case(s)` : 'No executable query plan.',
  facts:r => `${r.facts.length} source record(s) returned.`,
  execution:r => `Result: ${JSON.stringify(r.result.execution?.result ?? null)}`,
  validation:() => 'Recheck source membership, identity, reporting context and meaning bundle.',
  dmn:r => `${r.trace.rules.length} rule(s) evaluated · ${r.result.decision?.rule_ids?.join(', ') || 'no matched rule'}`,
  nlg:r => `${r.detail} · deterministic pySimpleNLG with truth and evidence checks.`,
  chatter:() => 'An exact, reviewed social response; no generated factual claim.'
};

function stageData(stage, r) {
  return {context:r.context,segments:r.annotation, query:{tree:r.tree, plan:r.plan}, facts:r.result.execution?.facts,
    execution:{result:r.result.execution?.result, outcome:r.result.execution?.outcome, reason:r.result.execution?.reason},
    validation:r.result.nlg_input, dmn:{decision:r.result.decision, evaluated_rules:r.trace.rules},
    nlg:r.result.nlg, chatter:r.result.chatter}[stage] ?? null;
}

function stepsView(r) {
  if (r.kind === 'render') {
    const original = journalTurns.find(t => t.id === r.render_of);
    const current = {...r,kind:'render_steps',trace:{...r.trace,steps:r.trace.steps.filter(s => s.status !== 'skipped')}};
    return `<h3 class="subheading">Original query execution</h3>` + (original ? stepsView(original) : empty('Original query trace is available in the query log.')) +
      `<h3 class="subheading">Linked ${esc(r.detail)} rendering</h3><p class="panel-intro">Original query ${esc(r.render_of)}. Rendering run ${esc(r.id)} rechecks the saved receipt; context is unchanged.</p>` + stepsView(current);
  }
  const order = ['context','segments','query','facts','execution','validation','chatter','dmn','nlg'];
  const steps = [...r.trace.steps].sort((a,b) => order.indexOf(a.stage) - order.indexOf(b.stage));
  return `<p class="panel-intro">Measured runtime work for this turn. Expand a step to inspect its actual output. Skipped steps have no duration.</p>` + steps.map((s,i) =>
    `<details class="step ${esc(s.status)}" ${s.status === 'blocked' ? 'open' : ''}><summary><span class="step-title">${String(i+1).padStart(2,'0')} &nbsp; ${esc(titles[s.stage] || s.stage)}</span><span class="step-time">${fmt(s.elapsed_us)}</span></summary>` +
    `${badge(s.status)}<p>${esc(s.reason || descriptions[s.stage]?.(r) || '')}</p>` +
    (s.started_at ? `<p>UTC ${esc(utc(s.started_at))} → ${esc(utc(s.ended_at))}</p>` : '') +
    (s.status !== 'skipped' ? raw('Inspect step output', stageData(s.stage,r)) : '') + `</details>`).join('');
}

function segmentsView(r) {
  if (!r.segments.length) return empty('No segments were generated.');
  return `<p class="panel-intro">Original text is preserved in order. Filler is reviewed framing; unknown or unplaced meaning prevents execution.</p><div class="segment-list">` +
    r.segments.map(s => `<div class="segment ${esc(s.type)}"><span class="text">${esc(s.text)}</span><small>${esc(s.id)} · ${esc(s.type)}</small></div>`).join('') + `</div>` +
    `<h3 class="subheading">Unplaced / ignored content</h3>${json({unplaced:r.annotation.unplaced || [], ignored:r.annotation.ignored || []})}` + raw('Full parser annotation',r.annotation);
}

function queryView(r) {
  return `<h3 class="subheading">Original question</h3><div class="output">${esc(r.query)}</div>` +
    `${r.resolved_query && r.resolved_query !== r.query ? `<h3 class="subheading">Resolved question</h3><div class="output">${esc(r.resolved_query)}</div>` : ''}<h3 class="subheading">Composed query tree</h3>${r.tree ? json(r.tree) : empty('No tree produced.')}` +
    `<h3 class="subheading">Checked execution plan</h3>${r.plan ? json(r.plan) : empty(r.result.reason || 'No executable plan. Only supported complete query shapes may run.')}`;
}

function factCards(facts) {
  return facts.map(f => {
    const source = f.source_record || f;
    return `<article class="fact-card"><h3>${esc(source.company || source.subject || f.entity)} · ${esc(source.predicate || f.metric)}</h3><div class="fact-value">${esc(f.value)}</div>` +
      `<p>Record ${esc(f.fact_id || f.id)}</p><p>Valid ${esc(source.valid_from)} → ${esc(source.valid_to || 'open')} (end exclusive)</p><p>Source: ${esc(source.source || f.source)}</p>` + raw('Full source record',f) + `</article>`;
  }).join('');
}

function factsView(r) {
  const decisionFacts = r.result.decision?.evidence || [];
  return `<p class="panel-intro">Returned records from the persisted fact store. Source values and IDs remain visible, including partial or ambiguous lookups.</p>` +
    (r.facts.length ? factCards(r.facts) : empty('No source facts returned for this question.')) +
    (decisionFacts.length ? `<h3 class="subheading">DMN binding evidence</h3>${factCards(decisionFacts)}` : '') +
    (r.result.nlg_input?.facts ? raw('NLG facts with reporting context and derivation operands',r.result.nlg_input.facts) : '');
}

function dmnView(r) {
  if (!r.dmn_model) return empty('No DMN policy was selected for this turn. Choose the illustrative annual revenue policy before sending a revenue question to inspect real rule evaluation.');
  const d = r.result.decision, model = r.dmn_model;
  const rows = model.rules.map(rule => {
    const evaluated = r.trace.rules.find(x => x.rule_id === rule.id);
    const hasError = evaluated?.tests.some(test => test.errors.length || test.value == null);
    return `<tr class="rule ${evaluated?.matched ? 'matched' : ''}"><td>${esc(rule.id)}</td><td>${Object.entries(rule.when).map(([k,v]) => `${esc(k)}: ${esc(v)}`).join('<br>')}</td><td>${esc(JSON.stringify(rule.then))}</td><td>${badge(evaluated ? hasError ? 'error / unknown' : evaluated.matched ? 'matched' : 'no match' : 'not run', hasError ? 'blocked' : evaluated?.matched ? 'completed' : 'skipped')}</td><td>${fmt(evaluated?.elapsed_us)}</td></tr>`;
  }).join('');
  return `<p class="panel-intro">Illustrative annual revenue review, bound to this query's company and fiscal year. Every row is checked under UNIQUE. This policy performs no action.</p>` +
    `<h3 class="subheading">${esc(model.id)} · UNIQUE</h3>` +
    (d ? `${badge(d.escalated ? 'escalated' : 'decided',d.escalated ? 'escalated' : 'completed')}${d.escalate_reason ? `<p class="warning">${esc(d.escalate_reason)} ${esc(d.reason || '')}</p>` : ''}<h3 class="subheading">Bound inputs</h3>${json(d.inputs)}<h3 class="subheading">Checked decision</h3>${json(d.decision)}` : empty('Rules were not run because the question did not reach the decision stage.')) +
    `<h3 class="subheading">Rule evaluation</h3><div class="table-scroll"><table><thead><tr><th>Rule</th><th>FEEL conditions</th><th>Output expressions</th><th>Result</th><th>Tests time</th></tr></thead><tbody>${rows}</tbody></table></div>` +
    `<h3 class="subheading">Condition results & time logs</h3>${r.trace.rules.length ? json(r.trace.rules) : empty('No rule conditions were evaluated. A policy or fact gate blocked evaluation.')}` +
    (d ? raw('Decision envelope, model hash, provenance & analysis',d) : '') + raw('Declared table & requirements',model);
}

function nlgView(r) {
  const s = r.trace.steps.find(x => x.stage === 'nlg');
  return `${badge(s?.status || 'skipped')}<h3 class="subheading">Final response</h3><div class="output">${esc(r.result.answer || r.result.reason || r.result.reason_code || 'No response rendered.')}</div>` +
    `<p class="panel-intro">${esc(r.result.route === 'chatter' ? 'Reviewed chatter text. NLG was skipped.' : s?.status === 'skipped' ? 'NLG was skipped. The refusal or escalation is preserved.' : `Detail: ${r.detail}. Checked factual text is composed with pySimpleNLG; successful decision output is appended as a checked dictionary.`)}</p>` +
    (r.result.warnings || []).map(w => `<p class="warning">${esc(w.message)}</p>`).join('') +
    (r.result.nlg_input ? raw('Meaning bundle sent to NLG',r.result.nlg_input) : '') +
    (r.result.nlg ? raw('NLG response, validation & clause trace',r.result.nlg) : '') + raw('Complete engine response',r.result);
}

function timeView(r) {
  return `<div class="time-summary"><div class="time-stat"><strong>${fmt(r.trace.total_us)}</strong><small>SERVER ENGINE REQUEST</small></div><div class="time-stat"><strong>${fmt(r.client_us)}</strong><small>BROWSER ROUND TRIP</small></div></div>` +
    `<p class="panel-intro">UTC wall-clock timestamps with monotonic elapsed times. Rule-test times are nested inside DMN time. Server time excludes queuing, serialization and network; browser time includes transport. Measurements include trace overhead.</p>` +
    `<p class="panel-intro">Started ${esc(r.trace.started_at)}<br>Ended ${esc(r.trace.ended_at)}</p>` +
    `<div class="table-scroll"><table><thead><tr><th>Stage</th><th>UTC start → end</th><th>Elapsed</th><th>Status</th></tr></thead><tbody>` +
    r.trace.steps.map(s => `<tr><td>${esc(titles[s.stage] || s.stage)}</td><td class="mono">${esc(utc(s.started_at))}<br>${esc(utc(s.ended_at))}</td><td class="mono">${fmt(s.elapsed_us)}</td><td>${badge(s.status)}</td></tr>`).join('') + `</tbody></table></div>` + raw('Full machine-readable time log',r.trace);
}

function inspect(index) {
  selected = index;
  document.querySelectorAll('.turn').forEach(el => el.classList.toggle('selected',Number(el.dataset.index) === selected));
  const r = turns[selected];
  $('inspection-query').textContent = r.query;
  $('inspection-meta').innerHTML = `${badge(r.result.outcome)}<span>${fmt(r.trace.total_us)} server</span><span>${esc(r.detail)}</span><span>Turn ${selected+1}</span>`;
  showTab(activeTab);
}

function showTab(tab) {
  activeTab = tab;
  document.querySelectorAll('[data-tab]').forEach(b => { b.setAttribute('aria-selected',String(b.dataset.tab === tab)); b.tabIndex = b.dataset.tab === tab ? 0 : -1; });
  $('trace-panel').setAttribute('aria-labelledby',`tab-${tab}`);
  if (tab === 'logs') { loadLogs(); return; }
  if (selected === null) return;
  const views = {steps:stepsView, segments:segmentsView, query:queryView, facts:factsView, dmn:dmnView, nlg:nlgView, time:timeView, context:contextView};
  $('trace-panel').innerHTML = views[tab](turns[selected]);
  $('trace-panel').scrollTop = 0;
}

function paintTurn(r, index) {
  const article = document.querySelector(`.turn[data-index="${index}"]`) || document.createElement('article');
  article.className = 'turn'; article.dataset.index = index;
  const e = r.result;
  article.innerHTML = `<div class="user-message"><p>${esc(r.query)}</p></div><div class="assistant-message"><div class="assistant-label"><strong>RESOCOR</strong><span>${esc(stamp(r.trace.started_at))}</span>${badge(e.outcome)}<span>${esc(r.detail)}</span></div>` +
    (r.context?.status === 'resolved' ? `<p class="context-note">Continuing: ${esc(r.resolved_query)}</p>` : '') +
    `<div class="message-answer">${esc(e.answer || e.reason || e.reason_code || 'No answer available.')}</div>` +
    (e.warnings || []).map(w => `<p class="warning">${esc(w.message)}</p>`).join('') +
    (e.nlg ? `<div class="answer-details" aria-label="Change this answer detail">${['crisp','detailed','audit'].map(d => `<button type="button" data-render="${index}" data-detail="${d}" aria-pressed="${r.detail === d}">${d[0].toUpperCase()+d.slice(1)}</button>`).join('')}</div>` : '') +
    `<div class="message-footer"><span>${fmt(r.trace.total_us)} · ${r.facts.length} fact(s) · ${r.trace.rules.length} rule(s) checked</span><button type="button" class="inspect-button" data-inspect="${index}">Inspect trace ↗</button></div><div class="trace-id">Run ${esc(r.id)}${r.render_of ? ` · query ${esc(r.render_of)}` : ''} · saved in SQLite</div></div>`;
  if (!article.parentElement) $('messages').append(article);
}

function addTurn(r) {
  if (r.kind === 'render') {
    const index = turns.findIndex(t => (t.render_of || t.id) === r.render_of);
    if (index >= 0) { turns[index] = r; paintTurn(r,index); inspect(index); }
    return;
  }
  const index = turns.push(r)-1;
  $('welcome')?.remove(); paintTurn(r,index);
  $('turn-count').textContent = `${turns.length} turn${turns.length === 1 ? '' : 's'}`;
  $('export').disabled = false; inspect(index);
  $('messages').scrollTop = $('messages').scrollHeight;
}

async function ask(question) {
  if (busy || !sessionId || !question.trim()) return;
  busy = true;
  setBusy(true);
  document.querySelectorAll('.example').forEach(b => b.disabled = true);
  $('messages').setAttribute('aria-busy','true');
  $('request-status').className = 'request-status';
  $('request-status').textContent = 'Reading the question, checking facts and recording the trace…';
  const start = performance.now();
  try {
    const request = {query:question,detail:getDetail(),policy:$('policy').value,conversation_id:sessionId};
    const key = JSON.stringify(request);
    if (!pendingRequest || pendingRequest.key !== key) pendingRequest = {key,id:crypto.randomUUID()};
    const res = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...request,request_id:pendingRequest.id})});
    const r = await res.json();
    if (!res.ok || r.error) throw new Error(r.error || `Request failed (${res.status}).`);
    journalTurns.push(r); pendingRequest = null;
    addTurn({...r,client_us:(performance.now()-start)*1000});
    showSessionState(r.state_after); refreshConversations().catch(() => {});
    if ($('question').value === question) $('question').value = '';
    $('request-status').textContent = '';
    $('connection').textContent = '● Engine ready';
  } catch (error) {
    $('request-status').className = 'request-status error';
    $('request-status').textContent = `${error.message} Your question is retained; send again to retry.`;
    $('question').value = question;
  } finally {
    busy = false;
    setBusy(false);
    document.querySelectorAll('.example').forEach(b => b.disabled = false);
    $('messages').setAttribute('aria-busy','false');
    $('question').focus();
  }
}

$('composer').addEventListener('submit',event => { event.preventDefault(); ask($('question').value); });
$('question').addEventListener('keydown',event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!busy) $('composer').requestSubmit(); } });
$('messages').addEventListener('click',event => {
  const renderButton = event.target.closest('[data-render]');
  if (renderButton) renderAnswer(Number(renderButton.dataset.render), renderButton.dataset.detail);
  const inspectButton = event.target.closest('[data-inspect]');
  if (inspectButton) { inspect(Number(inspectButton.dataset.inspect)); if (matchMedia('(max-width:760px)').matches) document.querySelector('.inspector').scrollIntoView({behavior:'smooth'}); }
  const example = event.target.closest('[data-example]');
  if (example) { $('question').value = example.dataset.example; ask(example.dataset.example); }
});
const tabs = [...document.querySelectorAll('[data-tab]')];
tabs.forEach((b,index) => {
  b.addEventListener('click',() => showTab(b.dataset.tab));
  b.addEventListener('keydown',event => {
    let next;
    if (event.key === 'ArrowRight') next = (index+1)%tabs.length;
    if (event.key === 'ArrowLeft') next = (index+tabs.length-1)%tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length-1;
    if (next !== undefined) { event.preventDefault(); showTab(tabs[next].dataset.tab); tabs[next].focus(); }
  });
});
const initialPanel = $('trace-panel').innerHTML;
const initialWelcome = $('messages').innerHTML;
let examples = [];
function renderExamples() {
  const node = $('examples');
  if (node) node.innerHTML = examples.slice(0,3).map(q => `<button type="button" class="example" data-example="${esc(q)}">${esc(q)}<span aria-hidden="true">↗</span></button>`).join('');
}
$('reset').addEventListener('click',() => { if (!busy) newConversation(); });
$('export').addEventListener('click',() => {
  if (!turns.length || !sessionId) return;
  // Download the server's stored JSON. Browser JSON.stringify converts e.g.
  // 255.0 to 255, so it must not reserialize content-hashed audit payloads.
  const a = document.createElement('a');
  a.href = `/api/export?id=${encodeURIComponent(sessionId)}`;
  a.download = `resocor-conversation-${sessionId}.json`;
  a.click();
});
