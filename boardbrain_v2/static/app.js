'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const token = location.hash.slice(1) || sessionStorage.getItem('bb-token') || '';
sessionStorage.setItem('bb-token', token);
history.replaceState(null, '', location.pathname);
let state = null, current = null, tab = 'bench', evidence = {}, seenJobs = new Map(), pendingQuestions = new Map(), refreshing = false, blobURLs = [], lastCaseSignature = '';
let modalCleanup = null;
const selectedPhotos = new Set();
const dismissedJobs = new Set();
const units = {voltage:'V',resistance:'Ω',diode:'V',current:'A',frequency:'Hz',continuity:''};
const modelLabel = model => ({'gpt-6.1-sol':'GPT-6.1 Sol','gpt-6-astra':'GPT-6 Astra','gpt-5.4':'GPT-5.4'}[model] || model);
function renderAnalysisHint() {
  if(!state) return;
  const deep=$('#deep-analysis').checked, model=deep?'gpt-6-astra':state.settings.model;
  $('#analysis-hint').textContent=`${modelLabel(model)} · ${deep?'Deeper analysis for difficult cases; allow more time.':'Standard analysis.'} ${model==='gpt-6-astra'?'$15':'$3'} temporarily reserved from your app budget; unused amount released after billing details arrive.`;
}
const powerLabel = p => ({unpowered:'Unpowered',standby:'Standby',attempting_boot:'Attempting boot',running:'Running',unknown:'State unknown'}[p] || p);
const date = value => new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
function notice(text) { $('#notice').textContent = text; $('#notice').hidden = !text; }
async function api(path, data) {
  const response = await fetch(path, {method:data===undefined?'GET':'POST',headers:{Authorization:`Bearer ${token}`,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'The request did not finish. Your saved data is still here.');
  return result;
}
async function binary(path, signal) {
  const response = await fetch(path, {headers:{Authorization:`Bearer ${token}`}, signal});
  if(!response.ok) throw Error((await response.json()).error);
  return response.blob();
}
function urlFor(blob) { const url = URL.createObjectURL(blob); blobURLs.push(url); return url; }
function clearModalResources() {
  if(modalCleanup) { const cleanup=modalCleanup; modalCleanup=null; cleanup(); }
  blobURLs.forEach(URL.revokeObjectURL); blobURLs=[];
}
function closeModal() { $('#modal').close(); clearModalResources(); }
function modal(title, html, setup, variant='') {
  clearModalResources(); $('#modal').className=variant;
  $('#modal-title').textContent = title; $('#modal-content').innerHTML = html;
  $('#modal-error').hidden = true;
  if (!$('#modal').open) $('#modal').showModal();
  if(setup) setup();
  const form = $('#modal form');
  if(form) form.addEventListener('submit', event => event.preventDefault());
}
function formAction(form, fn) {
  form.addEventListener('submit', async event => {
    event.preventDefault(); const button = form.querySelector('[type=submit]'); if(button) button.disabled = true;
    $('#modal-error').hidden = true;
    try { await fn(new FormData(form)); }
    catch(error) { $('#modal-error').textContent = error.message; $('#modal-error').hidden = false; }
    finally { if(button) button.disabled = false; }
  });
}
function field(name,label,value='',extra='') { return `<label class="field">${esc(label)}<input name="${name}" value="${esc(value)}" ${extra}></label>`; }
function footer(text) { return `<div class="form-footer"><button type="submit" class="primary">${text}</button></div>`; }
function caseId() { if(!current) throw Error('Open a workspace first.'); return current.id; }
function draftSave() { if(current) sessionStorage.setItem(`bb-draft-${current.id}`, $('#question').value); }
async function loadCase(id) {
  draftSave(); current = await api('/api/case?id='+encodeURIComponent(id));
  sessionStorage.setItem('bb-case', id); selectedPhotos.clear(); evidence={}; lastCaseSignature='';
  $('#deep-analysis').checked=false;
  $('#question').value = sessionStorage.getItem(`bb-draft-${id}`) || '';
  $('#lookup-results').innerHTML = '<p class="muted small">Search this repair’s evidence. Exact punctuation is preserved.</p>';
  render();
}
async function refresh() {
  if(refreshing) return;
  refreshing = true;
  try {
    state = await api('/api/state');
    if(current) {
      const updated = state.cases.find(c=>c.id===current.id);
      if(updated && (updated.updated!==current.updated || state.jobs.some(j=>j.case_id===current.id && ['queued','running','stopping'].includes(j.status)))) current = await api('/api/case?id='+current.id);
    }
    for(const job of state.jobs) {
      if(seenJobs.get(job.id) !== job.status && ['complete','failed','cancelled'].includes(job.status)) {
        if(job.case_id===current?.id) current = await api('/api/case?id='+current.id);
        if(job.status==='failed') notice(job.message);
        const pending = pendingQuestions.get(job.id);
        if(pending && job.status==='complete') {
          if(current?.id===pending.caseId && $('#question').value===pending.text) $('#question').value='';
          sessionStorage.removeItem(`bb-draft-${pending.caseId}`); selectedPhotos.clear();
        }
        pendingQuestions.delete(job.id);
      }
      seenJobs.set(job.id,job.status);
    }
    render();
  } catch(error) { $('#connection').textContent='Connection lost'; notice(error.message); }
  finally { refreshing=false; }
}
function render() {
  if(!state) return;
  $('#version').textContent=state.version;
  $('#connection').textContent=state.has_api_key?'AI configured · Local cases':'Local mode · AI setup needed';
  const u=state.usage;
  $('#budget').innerHTML=`<strong>$${u.estimated_cost.toFixed(2)}</strong> / $${Number(u.limit).toFixed(0)} this month<div class="meter"><progress max="${esc(u.limit)}" value="${esc(Math.min(u.limit,u.estimated_cost+u.reserved))}"></progress></div><span>${u.reserved?`$${u.reserved.toFixed(2)} reserved for a running call`:(u.estimated_cost>=u.warning_at?'Usage warning threshold reached':'Estimated AI usage · app limit')}</span>`;
  const query=$('#case-search').value.toLowerCase();
  const cases=state.cases.filter(c=>`${c.title} ${c.board_id} ${c.symptom}`.toLowerCase().includes(query));
  $('#case-count').textContent=state.cases.length;
  $('#case-list').innerHTML=cases.map(c=>`<button class="case-link ${current?.id===c.id?'active':''}" data-case="${c.id}"><strong>${esc(c.title)}</strong><small><span>${esc(c.board_id||c.device||'Board not identified')}</span><span>${c.kind==='reference'?'Reference':c.status==='confirmed'?'✓ Fixed':c.status==='unresolved'?'Unresolved':'Open'}</span></small></button>`).join('') || '<p class="muted small">No workspaces here yet.</p>';
  $('#welcome').hidden=!!current; $('#case-workspace').hidden=!current;
  if(!current) return;
  renderAnalysisHint();
  $('#case-title').textContent=current.title; $('#case-symptom').textContent=current.symptom;
  $('#case-meta').textContent=[current.device||'REPAIR',current.board_id||'BOARD UNKNOWN',current.revision?`REV ${current.revision}`:'REVISION UNKNOWN'].join('  /  ');
  const reference=current.kind==='reference';
  $('#case-status').textContent=reference?'Reference only':current.status==='confirmed'?'Repair confirmed':current.status;
  $('#outcome').hidden=reference; $('#start-from-reference').hidden=!reference;
  for(const id of ['add-reading','add-pin','find-cases','readings-panel']) $('#'+id).hidden=reference;
  $('#plan-label').textContent=reference?'EXPLORE THE REFERENCE':'NEXT USEFUL TEST';
  $('#question-label').textContent=reference?'What would you like to find?':'What are you seeing?';
  $('#question').placeholder=reference?'Ask about a component, net, or schematic page…':'Describe the symptom, ask about a reading, or tell me what changed…';
  $('#ask').textContent=reference?'Ask about these files ↑':'Find the next step ↑';
  $('#files-title').textContent=reference?'Board reference files':'Evidence for this repair';
  $('#history-title').textContent=reference?'Reference history':'Repair timeline';
  $('#outcome').textContent=current.status==='open'?'Record outcome':'Reopen repair';
  $('#workspace-label').textContent=current.title;
  $('#file-count').textContent=current.sources.length;
  $('#ask').disabled=current.status!=='open'||state.jobs.some(j=>j.kind==='assistant'&&['queued','running','stopping'].includes(j.status));
  for(const id of ['add-reading','add-note','add-pin','upload-button']) $('#'+id).disabled=current.status!=='open';
  const jobs=state.jobs.filter(j=>!dismissedJobs.has(j.id)&&j.case_id===current.id&&['queued','running','stopping','failed'].includes(j.status));
  $('#jobs').innerHTML=jobs.slice(-3).map(j=>`<div class="job ${j.status==='failed'?'failed':''}"><span>${esc(j.kind==='assistant'?'Assistant':'File import')} · ${esc(j.message)}</span>${j.status==='failed'?`<button class="quiet" data-dismiss="${j.id}">Dismiss</button>`:`<button class="quiet" data-cancel="${j.id}">Stop</button>`}</div>`).join('');
  const signature=JSON.stringify([current.id,current.updated,state.has_api_key,current.sources.map(s=>[s.id,s.status])]);
  if(signature!==lastCaseSignature){renderPlan(); renderReadings(); renderFiles(); renderTimeline();lastCaseSignature=signature;}
  $('#photo-selection').innerHTML=current.sources.filter(s=>s.role==='photo'&&s.status==='ready').map(s=>`<label class="photo-choice"><input type="checkbox" data-photo="${s.id}" ${selectedPhotos.has(s.id)?'checked':''}>${esc(s.name)}</label>`).join('');
  for(const name of ['bench','files','history']) {
    $('#'+name+'-tab').hidden=tab!==name;
    const button=$(`[data-tab="${name}"]`); button.classList.toggle('active',tab===name);button.setAttribute('aria-selected',String(tab===name));
  }
}
function currentPlan() { return current.events.filter(e=>e.kind==='plan').at(-1); }
function completed(test, planEvent) {
  return current.events.some(e=>e.kind==='measurement'&&!e.retracted&&e.created>planEvent.created&&e.data.target===test.target&&e.data.quantity===test.quantity&&e.data.power_state===test.power_state&&e.data.reference===test.reference&&test.reference&&e.data.power_state!=='unknown'&&!e.data.warnings?.length&&(test.quantity!=='diode'||(e.data.polarity===test.polarity&&test.polarity)));
}
function sourceChips(ids) {
  return [...new Set(ids||[])].filter(id=>evidence[id]).map(id=>`<button class="source-chip" data-evidence="${esc(id)}">${esc(evidenceLabel(id))}</button>`).join('');
}
function claimPin(claim) {
  return (claim.evidence_ids||[]).map(id=>evidence[id]).find(e=>e?.kind==='pin'&&e.data.ref===claim.ref&&String(e.data.pin)===String(claim.pin)&&e.data.net===claim.net);
}
function renderClaims(claims) {
  const labels={pin_connection:'Pin connection',document_fact:'Document excerpt',visual_observation:'Image observation',inference:'Interpretation',general:'General principle',unknown:'Not established'};
  return claims.length?`<section class="grounded-answer" aria-label="Answer and supporting sources">${claims.map(claim=>{
    const pin=claim.kind==='pin_connection'?claimPin(claim):null;
    const content=claim.kind==='pin_connection'?(pin?`<p class="claim-connection"><strong>${esc(pinLabel(pin.data))}</strong><span aria-hidden="true">→</span><strong class="claim-net">${esc(pin.data.net)}</strong></p><p class="claim-context">${pin.source_id?'Connection recorded in the boardview.':'Mapping recorded as checked by the technician.'}${pin.conflict?' Conflicting mappings need checking.':!pin.scope_confirmed?' File applicability to a physical board is unconfirmed.':''}</p>`:'<p>Pin connection not established by the attached sources.</p>'):`<p class="claim-text">${esc(claim.text)}</p>`;
    return `<article class="answer-claim claim-${esc(claim.kind)}"><h3>${esc(labels[claim.kind]||'Answer')}</h3>${content}${claim.quote?`<blockquote class="claim-quote">${esc(claim.quote)}</blockquote>`:''}${claim.kind==='visual_observation'?'<p class="claim-context">Interpreted from an image; check the original evidence before using it.</p>':''}${sourceChips(claim.evidence_ids)}</article>`;
  }).join('')}</section>`:'';
}
function renderPlanSources(plan, retrieved) {
  const claims=plan.claims||[], ids=Object.keys(retrieved||{});
  if(!ids.length) return '';
  if(!claims.length) return current.kind==='reference'?`<div class="panel form-gap"><h3>Evidence retrieved</h3>${sourceChips(ids)}</div>`:'';
  const used=[...new Set([...claims.flatMap(c=>c.evidence_ids||[]),...plan.tests.flatMap(t=>[t.pin_evidence_id,t.expected_evidence_id,...t.evidence_ids]),...plan.hypotheses.flatMap(h=>[...h.support,...h.contradictions])])].filter(id=>retrieved[id]);
  const other=ids.filter(id=>!used.includes(id));
  return `<div class="panel form-gap plan-sources"><h3>Sources used</h3>${used.length?sourceChips(used):'<p class="small muted">No imported source supports these statements.</p>'}${other.length?`<details class="source-details other-evidence"><summary>Other evidence retrieved (${other.length})</summary><p class="small muted">Retrieved during this answer, but not cited as support.</p>${sourceChips(other)}</details>`:''}</div>`;
}
function renderPlan() {
  const event=currentPlan(); evidence={...evidence,...(event?.data.evidence||{})};
  if(!event) {
    $('#plan').innerHTML=`<div class="empty-plan"><h2>${current.kind==='reference'?'Your board reference is ready to explore.':'Let’s establish the starting point.'}</h2><p>${current.kind==='reference'?'Search a component or net below, or open Files & photos to inspect the schematic and import details. No repair outcome is needed.':'Record what you’ve already checked, then ask for the next test. Add the boardview or a clear photo whenever you have one.'}</p><div class="small">${state.has_api_key?'Your observations stay saved between sessions.':'You can start saving evidence now. Add your API key in Settings for AI guidance.'}</div></div>`;
    $('#hypotheses').innerHTML=''; return;
  }
  const plan=event.data.plan, stale=event.data.stale||current.updated>event.created;
  $('#plan').innerHTML=(stale?'<div class="warning">This plan predates a case update. Completed readings are shown below; ask for an update before following an old next step.</div>':'')+plan.tests.map((t,i)=>{
    const done=completed(t,event);
    return `<article class="next-test"><div class="test-heading"><span class="step-number">${done?'✓':String(i+1).padStart(2,'0')}</span><h2>${esc(t.title)}</h2></div><div class="test-facts"><span>${esc(t.quantity)}</span>${t.target?`<span>${esc(evidence[t.pin_evidence_id]?.kind==='pin'?pinLabel(evidence[t.pin_evidence_id].data):t.target)}</span>${evidence[t.pin_evidence_id]?.data.net?`<span class="mono">${esc(evidence[t.pin_evidence_id].data.net)}</span>`:''}`:''}<span>${esc(powerLabel(t.power_state))}</span></div><p class="procedure">${esc(t.procedure)}</p>${t.reference?`<p class="small">Reference: ${esc(t.reference)}${t.polarity?` · ${esc(t.polarity)}`:''}</p>`:''}<details class="test-details"><summary>Why this test & what it tells us</summary><p>${esc(t.why)}</p><p>${esc(t.interpretation)}</p><p>${t.expected_quote?`Source excerpt: “${esc(t.expected_quote)}”`:'No verified numeric expected value is attached to this test.'}</p>${t.stop_if?`<p>Stop if: ${esc(t.stop_if)}</p>`:''}${[t.pin_evidence_id,t.expected_evidence_id,...t.evidence_ids].filter((x,i,a)=>x&&a.indexOf(x)===i).map(x=>`<button class="source-chip" data-evidence="${esc(x)}">${esc(evidenceLabel(x))}</button>`).join('')}</details>${t.quantity!=='observation'?`<button class="${done?'quiet':'secondary'}" data-test="${i}" ${stale&&!done?'disabled':''}>${done?'Reading recorded':'Record this reading'}</button>`:''}</article>`;
  }).join('')+renderClaims(plan.claims||[])+`${current.kind==='reference'&&plan.claims?.length?'':`<div class="summary">${esc(plan.summary)}</div>`}${renderPlanSources(plan,event.data.evidence)}${event.data.model?`<p class="microcopy">${esc(modelLabel(event.data.model))} · ${event.data.analysis_mode==='deep'?'Deeper':'Standard'} analysis · Estimated $${Number(event.data.estimated_cost||0).toFixed(3)} · ${Number(event.data.elapsed_seconds||0).toFixed(1)} s</p>`:''}`+(plan.questions.length?`<div class="questions"><h3>To narrow this down</h3>${plan.questions.map(q=>`<p>${esc(q)}</p>`).join('')}</div>`:'');
  $('#hypotheses').innerHTML=(plan.hypotheses.length?`<div class="hypotheses"><div class="section-label">WORKING HYPOTHESES</div>${plan.hypotheses.map(h=>`<div class="hypothesis">${esc(h.idea)}<br><span>Support: ${h.support.length} sources · Contradictions: ${h.contradictions.length}</span>${[...h.support,...h.contradictions].filter((x,i,a)=>a.indexOf(x)===i).map(x=>`<button class="source-chip" data-evidence="${esc(x)}">${esc(evidenceLabel(x))}</button>`).join('')}</div>`).join('')}</div>`:'')+(plan.web_sources.length?`<div class="panel"><h3>Research references · verify applicability</h3>${plan.web_sources.map(s=>`<p class="small"><a href="${esc(s.url)}" target="_blank" rel="noreferrer noopener">${esc(s.title||s.url)} ↗</a></p>`).join('')}</div>`:'');
}
function pinLabel(d) { return `${d.ref}.${d.name||d.pin}`; }
function documentLabel(d) { return `PDF page ${d.page}${d.sheet?` · schematic sheet ${d.sheet}`:''}${d.page_title?` · ${d.page_title}`:''}`; }
function imageIsSection(d) { return Array.isArray(d.region)&&d.region.length===4&&d.region.some((n,i)=>n!==[0,0,1,1][i]); }
function inspectionLabel(d) { return `PDF page ${d.page} · ${imageIsSection(d)?'enlarged section':'image'}${d.sheet?` · schematic sheet ${d.sheet}`:''}`; }
function evidenceLabel(id) { const e=evidence[id];return e?.kind==='document'?documentLabel(e.data):e?.kind==='document_image'?inspectionLabel(e.data):e?.kind==='pin'?pinLabel(e.data):e?.kind==='measurement'?e.data.target:e?.name||'Source unavailable'; }
function readingLabel(d) { return `${d.value} ${d.unit}`.trim(); }
function renderReadings() {
  const readings=current.events.filter(e=>e.kind==='measurement'&&!e.retracted).slice(-6).reverse();
  $('#readings').innerHTML=readings.map(e=>`<div class="reading"><div class="reading-top"><span>${esc(e.data.target)}</span><b>${esc(readingLabel(e.data))}</b></div><small>${esc(e.data.quantity)} · ${esc(powerLabel(e.data.power_state))}${e.data.warnings?.length?' · Needs context':''}</small></div>`).join('')||'<p class="muted small">No readings yet. Every saved reading includes its mode and conditions.</p>';
}
function renderFiles() {
  $('#source-list').innerHTML=current.sources.map(s=>`<article class="source-row"><div><h3>${esc(s.name)}</h3><span class="pill ${['unsupported','invalid','partial','interrupted'].includes(s.status)?'warn':'dim'}">${esc(s.status)}</span> <span class="small muted">${esc(s.role)} · ${s.scope_confirmed?'Board/revision matched':'Board scope unconfirmed'}</span><p>${esc(s.info.message||'Waiting for import')}</p></div><div class="source-controls">${s.info.issues?.length?`<button class="quiet" data-import-issues="${s.id}">Review ${s.info.excluded_part_count} excluded parts</button>`:''}${s.role==='photo'&&s.status==='ready'?`<button class="quiet" data-view-photo="${s.id}">View photo</button>`:''}${s.name.toLowerCase().endsWith('.pdf')?`<button class="quiet" data-view-pdf="${s.id}">View pages</button>`:''}<button class="quiet" data-scope="${s.id}">Check scope</button><button class="quiet" data-open-source="${s.id}">Open on Mac</button><button class="quiet" data-download="${s.id}">Save file</button>${['invalid','interrupted','partial','unsupported'].includes(s.status)?`<button class="quiet" data-retry="${s.id}">${s.status==='partial'&&s.name.toLowerCase().endsWith('.pdf')?'OCR pages':'Retry import'}</button>`:''}</div></article>`).join('')||'<div class="empty-plan"><h2>No files? We can still start.</h2><p>Record the symptom and observations. Add a clear photo, then check component markings and test points as you go.</p></div>';
}
function renderTimeline() {
  $('#timeline').innerHTML=[...current.events].reverse().map(e=>{
    let title=e.kind, body=''; const d=e.data;
    if(e.kind==='measurement'){title=`${d.target} · ${readingLabel(d)}`;body=`${d.quantity} · ${powerLabel(d.power_state)}\nReference: ${d.reference||'Not recorded'}${d.polarity?' · '+d.polarity:''}\n${d.note||''}${d.warnings?.length?'\n'+d.warnings.join('\n'):''}`;}
    else if(e.kind==='message'){title='You asked';body=d.text;}
    else if(e.kind==='note'){title='Observation';body=d.text;}
    else if(e.kind==='pin'){title=`Checked mapping · ${d.ref}.${d.pin} → ${d.net}`;body=d.basis;}
    else if(e.kind==='plan'){title=d.model?`Assistant plan · ${modelLabel(d.model)}`:'Assistant plan';body=d.plan.summary;}
    else if(e.kind==='outcome'){title=d.status==='confirmed'?'Repair confirmed':d.status==='open'?'Repair reopened':'Repair unresolved';body=d.status==='confirmed'?`Fix: ${d.fix}\nVerification: ${d.verification}\nRoot cause: ${d.cause_status}${d.root_cause?' — '+d.root_cause:''}`:d.reason||'';}
    return `<article class="timeline-item ${e.retracted?'retracted':''}"><div class="stamp">${esc(date(e.created))}${e.retracted?' · Retracted':''}</div><h3>${esc(title)}</h3><p>${esc(body)}</p>${['measurement','note','pin'].includes(e.kind)&&!e.retracted&&current.status==='open'?`<button class="quiet small" data-retract="${e.id}">Retract incorrect entry</button>`:''}</article>`;
  }).join('')||'<p class="muted">The first observation starts your timeline.</p>';
}
function newCase(kind='repair', reference=null) {
  const isRef=kind==='reference';
  modal(isRef?'Add board reference':'Start a repair',`<form id="new-form"><div class="fields"><div class="full">${field('title',isRef?'Reference title':'Repair title',reference?`${reference.device||reference.board_id} — `:'','required maxlength="120"')}</div>${field('device','Device / model',reference?.device||'','placeholder="MacBook Pro A…"')}${field('board_id','Board number',reference?.board_id||'','placeholder="820-…"')}${field('revision','Board revision',reference?.revision||'','placeholder="Leave blank if unknown"')}<label class="field full">${isRef?'Notes (optional)':'Symptom'}<textarea name="symptom" ${isRef?'':'required'} maxlength="4000" placeholder="${isRef?'Reference material for exploring and validating the files.':'What happens when power is connected? What have you already tried?'}"></textarea></label></div>${reference?'<p class="modal-copy form-gap">The original files and their imported evidence are reused. Scope checks must be confirmed for this physical board. Reference notes and AI answers stay in the reference workspace.</p>':''}${footer(isRef?'Open reference':'Open repair')}</form>`,()=>formAction($('#new-form'),async form=>{
    const created=await api('/api/cases',{...Object.fromEntries(form),kind,...(reference?{reference_id:reference.id}:{})});closeModal();await refresh();tab=isRef?'files':'bench';await loadCase(created.id);
  }));
}
function editCase() {
  modal(current.kind==='reference'?'Reference details':'Repair details',`<form id="edit-form"><div class="fields"><div class="full">${field('title',current.kind==='reference'?'Reference title':'Repair title',current.title,'required maxlength="120"')}</div>${field('device','Device / model',current.device)}${field('board_id','Board number',current.board_id)}${field('revision','Board revision',current.revision)}<label class="field full">${current.kind==='reference'?'Notes':'Symptom'}<textarea name="symptom" required>${esc(current.symptom)}</textarea></label></div><p class="modal-copy form-gap">Changing the board number or revision resets file scope checks and retracts checked pin mappings. Recheck them against the corrected board identity.</p>${footer('Save details')}</form>`,()=>formAction($('#edit-form'),async form=>{await api('/api/case-details',{case_id:caseId(),...Object.fromEntries(form)});closeModal();await refresh();}));
}
function settings() {
  const s=state.settings;
  modal('Settings & equipment',`<form id="settings-form"><p class="modal-copy">Cases and files are stored on this Mac. AI requests use your OpenAI API account. Your key is saved in macOS Keychain.</p><div class="fields"><label class="field full">OpenAI API key<input name="api_key" type="password" autocomplete="new-password" placeholder="${state.has_api_key?'Key saved · leave blank to keep it':'sk-…'}"><span class="hint">Never included in case exports or backups.</span></label><label class="field full">Standard analysis model<select name="model"><option value="gpt-6.1-sol" ${s.model==='gpt-6.1-sol'?'selected':''}>GPT-6.1 Sol</option><option value="gpt-6-astra" ${s.model==='gpt-6-astra'?'selected':''}>GPT-6 Astra</option>${s.model==='gpt-5.4'?'<option value="gpt-5.4" selected>GPT-5.4 (previous preview setting)</option>':''}</select><span class="hint">Default: GPT-6.1 Sol. Deeper analysis uses GPT-6 Astra. Account access and repair performance still need validation.</span></label>${field('monthly_limit','Monthly app limit ($)',s.monthly_limit,'type="number" min="1" max="1000" required')}${field('warning_at','Warn at ($)',s.warning_at,'type="number" min="0" required')}<label class="field full">Your bench equipment<textarea name="equipment" rows="5" required>${esc(s.equipment)}</textarea><span class="hint">Add the exact scope, probes and logic-analyzer models when available.</span></label>${field('flexbv_version','FlexBV version',s.flexbv_version,'required')}</div><p class="modal-copy form-gap">Usage is a conservative app estimate, not your provider’s invoice. A request temporarily reserves $3 for Sol or $15 for Astra; unused reservation is released when billing details arrive. An interrupted connection keeps the reservation as an uncertain charge. Other apps using the same key are not counted.</p>${footer('Save settings')}</form>`,()=>formAction($('#settings-form'),async form=>{
    const data=Object.fromEntries(form);data.monthly_limit=Number(data.monthly_limit);data.warning_at=Number(data.warning_at);await api('/api/settings',data);closeModal();await refresh();notice('Settings saved.');
  }));
}
function addReading(test=null) {
  const d=test||{target:'',quantity:'voltage',power_state:'unknown',reference:'',polarity:''};
  modal('Record a reading',`<form id="reading-form"><label class="field">Quick entry<div class="inline form-gap"><input id="quick-reading" placeholder="PP3V3 = 3.28 V" aria-label="Quick reading"><button id="parse-reading" type="button" class="quiet">Fill fields</button></div><span class="hint">Also accepts U7000.1 = 12 kΩ or USB_D+ = diode 0.42 V. Review the fields before saving.</span></label><div class="fields form-gap">${field('target','Net, pin or physical point',d.target,'required')}<label class="field">Measurement type<select name="quantity">${Object.keys(units).map(q=>`<option value="${q}" ${q===d.quantity?'selected':''}>${q}</option>`).join('')}</select></label>${field('value','Reading (base unit; OL or PASS/FAIL)', '', 'required placeholder="0.00"')}<label class="field">Power state<select name="power_state">${['unknown','unpowered','standby','attempting_boot','running'].map(p=>`<option value="${p}" ${p===d.power_state?'selected':''}>${powerLabel(p)}</option>`).join('')}</select></label>${field('reference','Probe reference / return',d.reference,'placeholder="Board ground"')}${field('polarity','Probe polarity (required for diode)',d.polarity,'placeholder="Red on ground, black on target"')}<label class="field full">Observation / conditions<textarea name="note" rows="2" placeholder="Battery disconnected, adapter attached, meter range…"></textarea></label></div><p id="reading-unit" class="modal-copy form-gap">Store this reading in ${esc(units[d.quantity]||'PASS / FAIL')}. Do not measure resistance, diode or continuity on a powered board.</p><input type="hidden" name="raw" value="">${footer('Save reading')}</form>`,()=>{
    const form=$('#reading-form');
    form.elements.quantity.addEventListener('change',()=>$('#reading-unit').textContent=`Store this reading in ${units[form.elements.quantity.value]||'PASS / FAIL'}. Record the actual conditions; do not guess.`);
    $('#parse-reading').onclick=async()=>{try{const parsed=await api('/api/parse-reading',{text:$('#quick-reading').value});for(const k of ['target','quantity','value','raw']) form.elements[k].value=parsed[k];form.elements.quantity.dispatchEvent(new Event('change'));$('#modal-error').hidden=true;}catch(e){$('#modal-error').textContent=e.message;$('#modal-error').hidden=false;}};
    formAction(form,async values=>{const data=Object.fromEntries(values);data.unit=units[data.quantity];const saved=await api('/api/observations',{case_id:caseId(),kind:'measurement',data});closeModal();await refresh();notice(saved.data.warnings.length?'Reading saved. '+saved.data.warnings.join(' '):'Reading saved.');});
  });
}
function addNote() {
  modal('Add an observation',`<form id="note-form"><label class="field">What did you observe?<textarea name="text" rows="6" required placeholder="Visual damage, startup behavior, a test result or a correction…"></textarea></label>${footer('Save observation')}</form>`,()=>formAction($('#note-form'),async form=>{await api('/api/observations',{case_id:caseId(),kind:'note',data:Object.fromEntries(form)});closeModal();await refresh();}));
}
function addPin() {
  modal('Record a checked pin mapping',`<form id="pin-form"><p class="modal-copy">Use a mapping you have checked in the matching FlexBV boardview or established by continuity. Preserve the exact net name.</p><div class="fields">${field('ref','Component','','required placeholder="U7000"')}${field('pin','Pin number','','required placeholder="1"')}<div class="full">${field('net','Exact net name','','required placeholder="USB_D+"')}</div><label class="field full">How did you verify it?<textarea name="basis" required placeholder="Board number/revision, FlexBV component and pin, or continuity test conditions…"></textarea></label><label class="check full"><input name="confirmed" type="checkbox" required> I checked this mapping on the board being repaired.</label></div>${footer('Save checked mapping')}</form>`,()=>formAction($('#pin-form'),async form=>{const data=Object.fromEntries(form);data.confirmed=form.has('confirmed');await api('/api/observations',{case_id:caseId(),kind:'pin',data});closeModal();await refresh();}));
}
function outcome() {
  if(current.status!=='open') return api('/api/outcome',{case_id:caseId(),status:'open'}).then(refresh).catch(e=>notice(e.message));
  modal('Record the outcome',`<form id="outcome-form"><label class="field">Result<select name="status"><option value="confirmed">Repair confirmed</option><option value="unresolved">Unresolved / not economical</option></select></label><div id="confirmed-fields" class="fields form-gap"><label class="field full">What fixed it?<textarea name="fix" placeholder="Actual repair performed"></textarea></label><label class="field full">Post-repair verification<textarea name="verification" placeholder="Measurements, boot tests, charging, load and soak testing…"></textarea></label><label class="field full">Root cause certainty<select name="cause_status"><option value="unknown">Root cause still unknown</option><option value="suspected">Suspected cause</option><option value="verified">Cause verified with evidence</option></select></label><label class="field full">Root cause / supporting evidence<textarea name="root_cause"></textarea></label><label class="check full"><input name="technician_confirmed" type="checkbox"> I verified this repair worked.</label></div><label id="unresolved-fields" class="field form-gap" hidden>Reason / last known state<textarea name="reason"></textarea></label><p class="modal-copy form-gap">Confirmed cases are searchable references. Saving a case does not automatically train a model.</p>${footer('Save outcome')}</form>`,()=>{
    const f=$('#outcome-form');f.elements.status.onchange=()=>{$('#confirmed-fields').hidden=f.elements.status.value!=='confirmed';$('#unresolved-fields').hidden=f.elements.status.value==='confirmed';};
    formAction(f,async values=>{const data=Object.fromEntries(values);data.technician_confirmed=values.has('technician_confirmed');await api('/api/outcome',{case_id:caseId(),...data});closeModal();await refresh();});
  });
}
function upload() {
  const scoped=!!(current.board_id&&current.revision);
  modal('Add evidence',`<form id="upload-form"><div class="fields"><label class="field full">File type<select name="role"><option value="photo">Board / microscope photo</option><option value="boardview">Boardview</option><option value="schematic">Schematic PDF</option><option value="reference">Datasheet / reference PDF</option></select></label><label class="field full">Choose files<input name="files" type="file" multiple required><span class="hint">Up to 25 MB each. HEIC, JPEG, PNG, PDF and boardview files can be retained.</span></label><label class="check full"><input name="scope_confirmed" type="checkbox" ${scoped?'':'disabled'}> These files match ${esc(current.board_id||'the board')} / ${esc(current.revision||'revision unknown')}.</label></div><p class="modal-copy form-gap">${scoped?'Confirm the file’s actual board revision, not just a similar model.':'Without a known board number and revision, imported mappings remain unconfirmed. You can record specific checked pin mappings separately.'} Supported connectivity: plain BVR3 and the documented ref,pin,net CSV format. Malformed components are excluded and listed in the import details. Other formats remain available in FlexBV until their adapters are validated.</p>${footer('Import files')}</form>`,()=>formAction($('#upload-form'),async form=>{
    const files=form.getAll('files');const id=caseId();
    for(const file of files){if(!file.size||file.size>25*1024*1024) throw Error(`${file.name}: choose a non-empty file up to 25 MB.`);const content=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(Error('File could not be read'));r.readAsDataURL(file);});await api('/api/upload',{case_id:id,filename:file.name,role:form.get('role'),scope_confirmed:form.has('scope_confirmed'),content});}
    closeModal();await refresh();notice('Files saved. Imports are running in the background.');
  }));
}
async function showPhoto(id) {
  const source=current.sources.find(s=>s.id===id), cid=caseId();
  modal(source.name,'<div id="photo-image" class="muted">Loading photo…</div>');
  const holder=$('#photo-image');
  try {
    const blob=await binary(`/api/photo?case_id=${cid}&id=${id}`);
    if(holder.isConnected) holder.innerHTML=`<img class="photo-preview" src="${urlFor(blob)}" alt="Uploaded board photo">`;
  } catch(error) { if(holder.isConnected) { $('#modal-error').textContent=error.message;$('#modal-error').hidden=false; } }
}
async function showPDF(id,page=1) {
  const source=current.sources.find(s=>s.id===id);
  if(!source) throw Error('Open the workspace containing this PDF first.');
  const cid=caseId(), pages=source.info.pages||1500;
  modal(source.name,`<p id="pdf-page-label" class="modal-copy"></p><div class="pdf-toolbar"><div class="inline"><button id="pdf-prev" class="quiet" type="button" aria-label="Previous page">←</button><button id="pdf-next" class="quiet" type="button" aria-label="Next page">→</button><form id="page-form" class="inline"><label for="pdf-page-number">PDF page</label><input id="pdf-page-number" name="page" type="number" value="${page}" min="1" max="${pages}" required aria-label="PDF page number"><span>of ${pages}</span><button class="secondary" type="submit">Go to page</button></form></div><div class="inline"><button id="pdf-fit" class="quiet" type="button">Fit width</button><button id="pdf-zoom-out" class="quiet" type="button" aria-label="Zoom out">−</button><select id="pdf-zoom" aria-label="Schematic zoom">${[.5,1,1.5,2,3,4].map(z=>`<option value="${z}" ${z===1?'selected':''}>${z*100}%</option>`).join('')}</select><button id="pdf-zoom-in" class="quiet" type="button" aria-label="Zoom in">+</button><button class="quiet" data-open-source="${esc(id)}" type="button">Open original PDF</button></div></div><div class="pdf-status-line"><span id="pdf-status" role="status" aria-live="polite"></span><span>Zoom in, then scroll to inspect the drawing.</span></div><div id="pdf-viewport" tabindex="0" aria-label="Schematic page; scroll to pan"><img id="pdf-page-image" alt="" hidden></div>`,null,'pdf-dialog');
  const viewport=$('#pdf-viewport'), img=$('#pdf-page-image'), label=$('#pdf-page-label'), status=$('#pdf-status'), input=$('#pdf-page-number');
  const zooms=[.5,1,1.5,2,3,4];
  let active=true, controller=null, sequence=0, url=null, zoom=1, shownPage=0, renderedEdge=0, timer=null;
  function sizeImage() { img.style.width=`${Math.round(Math.max(100,viewport.clientWidth-24)*zoom)}px`; }
  function resolution() { return Math.round(Math.min(6000,Math.max(2200,(viewport.clientWidth-24)*zoom*(window.devicePixelRatio||1)*Math.max(1,img.naturalHeight/(img.naturalWidth||1))))); }
  async function loadPage(next, force=false) {
    if(!Number.isInteger(next)||next<1||next>pages) { $('#modal-error').textContent=`Choose a PDF page from 1 to ${pages}.`;$('#modal-error').hidden=false;return; }
    const changing=next!==shownPage;
    page=next;input.value=page;
    label.textContent=documentLabel({page,...(source.info.sheet_index?.[String(page)]||{})});
    $('#pdf-prev').disabled=page===1;$('#pdf-next').disabled=page===pages;
    sizeImage();const edge=resolution();
    if(!changing&&!force&&renderedEdge>=edge) return;
    if(controller) controller.abort();controller=new AbortController();const ticket=++sequence;
    if(changing) { img.hidden=true;viewport.scrollTo(0,0); }
    $('#modal-error').hidden=true;status.textContent=changing?`Loading PDF page ${page}…`:'Sharpening page…';
    viewport.setAttribute('aria-busy','true');
    let candidate=null;
    try {
      const blob=await binary(`/api/page?case_id=${encodeURIComponent(cid)}&id=${encodeURIComponent(id)}&page=${page}&max_edge=${edge}`,controller.signal);
      if(!active||ticket!==sequence) return;
      candidate=URL.createObjectURL(blob);
      const ready=new Image();ready.src=candidate;await ready.decode();
      if(!active||ticket!==sequence) { URL.revokeObjectURL(candidate);return; }
      if(url) URL.revokeObjectURL(url);url=candidate;candidate=null;
      img.src=url;img.alt=`Schematic PDF page ${page}`;img.hidden=false;sizeImage();
      shownPage=page;renderedEdge=edge;
      status.textContent=`PDF page ${page} loaded · ${Math.round(zoom*100)}%`;
    } catch(error) {
      if(candidate) URL.revokeObjectURL(candidate);
      if(!active||ticket!==sequence||error.name==='AbortError') return;
      status.textContent='Page could not load. Use Go to page to retry.';
      $('#modal-error').textContent=error.message;$('#modal-error').hidden=false;
    } finally { if(active&&ticket===sequence) viewport.setAttribute('aria-busy','false'); }
  }
  function setZoom(next) {
    const before=zoom;zoom=next;$('#pdf-zoom').value=String(zoom);sizeImage();
    viewport.scrollLeft=(viewport.scrollLeft+viewport.clientWidth/2)*zoom/before-viewport.clientWidth/2;
    viewport.scrollTop=(viewport.scrollTop+viewport.clientHeight/2)*zoom/before-viewport.clientHeight/2;
    $('#pdf-zoom-out').disabled=zoom===zooms[0];$('#pdf-zoom-in').disabled=zoom===zooms[zooms.length-1];
    status.textContent=`PDF page ${page} · ${Math.round(zoom*100)}%`;
    clearTimeout(timer);timer=setTimeout(()=>loadPage(page),120);
  }
  $('#page-form').addEventListener('submit',event=>{event.preventDefault();loadPage(Number(input.value),true);});
  $('#pdf-prev').onclick=()=>loadPage(page-1);$('#pdf-next').onclick=()=>loadPage(page+1);
  $('#pdf-zoom').onchange=event=>setZoom(Number(event.target.value));
  $('#pdf-zoom-in').onclick=()=>setZoom(zooms[Math.min(zooms.length-1,zooms.indexOf(zoom)+1)]);
  $('#pdf-zoom-out').onclick=()=>setZoom(zooms[Math.max(0,zooms.indexOf(zoom)-1)]);
  $('#pdf-fit').onclick=()=>{setZoom(1);viewport.scrollTo(0,0);};
  const resize=()=>{sizeImage();clearTimeout(timer);timer=setTimeout(()=>loadPage(page),180);};
  window.addEventListener('resize',resize);
  modalCleanup=()=>{active=false;sequence++;clearTimeout(timer);controller?.abort();if(url)URL.revokeObjectURL(url);window.removeEventListener('resize',resize);};
  await loadPage(Number(page));
}
async function showInspection(e) {
  const cid=caseId(), d=e.data;
  const section=imageIsSection(d), bounds=section?d.region.map(n=>Math.round(n*100)):null;
  modal(e.name||'Inspected schematic image',`<p class="inspection-label">${esc(inspectionLabel(d))}${d.page_title?` · ${esc(d.page_title)}`:''}</p><div class="inspection-context"><p>Image sent to the assistant; interpretation still needs checking.</p><p class="small muted">${section?`Section of the full drawing: ${bounds[0]}–${bounds[2]}% across, ${bounds[1]}–${bounds[3]}% down.`:'Full schematic page.'}${d.focus?` Focus: ${esc(d.focus)}.`:''}</p></div><div class="pdf-toolbar"><div class="inline"><button id="inspection-fit" class="quiet" type="button">Fit width</button><button id="inspection-pixels" class="quiet" type="button">Actual pixels</button><button id="inspection-retry" class="quiet" type="button" hidden>Retry image</button></div><button id="inspection-full-page" class="secondary" type="button">Open full page</button></div><p id="inspection-status" class="modal-copy" role="status" aria-live="polite">Loading inspected image…</p><div id="inspection-viewport" tabindex="0" aria-label="Image inspected by the assistant; scroll to pan"><img id="inspection-image" alt="" hidden></div>`,null,'pdf-dialog inspection-dialog');
  const viewport=$('#inspection-viewport'),img=$('#inspection-image'),status=$('#inspection-status'),retry=$('#inspection-retry');
  let active=true,controller=null,url=null,sequence=0,actualPixels=false;
  function sizeImage() { img.style.width=actualPixels?`${img.naturalWidth}px`:'100%'; }
  async function loadImage() {
    controller?.abort();controller=new AbortController();const ticket=++sequence;
    retry.hidden=true;$('#modal-error').hidden=true;status.textContent='Loading inspected image…';viewport.setAttribute('aria-busy','true');
    let candidate=null;
    try {
      const blob=await binary(`/api/inspection?case_id=${encodeURIComponent(cid)}&id=${encodeURIComponent(e.id)}`,controller.signal);
      if(!active||ticket!==sequence) return;
      candidate=URL.createObjectURL(blob);const ready=new Image();ready.src=candidate;await ready.decode();
      if(!active||ticket!==sequence){URL.revokeObjectURL(candidate);return;}
      if(url)URL.revokeObjectURL(url);url=candidate;candidate=null;
      img.src=url;img.alt=`${section?'Enlarged section of':'Full'} schematic PDF page ${d.page} inspected by the assistant`;img.hidden=false;sizeImage();
      status.textContent='Inspected image loaded. Open the full page to see its surrounding circuit.';
    } catch(error) {
      if(candidate)URL.revokeObjectURL(candidate);
      if(!active||ticket!==sequence||error.name==='AbortError')return;
      status.textContent='Inspected image could not load.';retry.hidden=false;
      $('#modal-error').textContent=error.message;$('#modal-error').hidden=false;
    } finally {if(active&&ticket===sequence)viewport.setAttribute('aria-busy','false');}
  }
  $('#inspection-fit').onclick=()=>{actualPixels=false;sizeImage();viewport.scrollTo(0,0);};
  $('#inspection-pixels').onclick=()=>{actualPixels=true;sizeImage();};
  $('#inspection-full-page').onclick=()=>showPDF(e.source_id,d.page).catch(error=>{notice(error.message);});
  retry.onclick=loadImage;
  modalCleanup=()=>{active=false;sequence++;controller?.abort();if(url)URL.revokeObjectURL(url);};
  await loadImage();
}
async function showEvidence(id) {
  const e=evidence[id];if(!e) throw Error('Evidence is no longer in the current view. Search it again.');
  if(e.kind==='document_image'&&e.source_id) return showInspection(e);
  if(e.kind==='document'&&e.source_id) return showPDF(e.source_id,e.data.page);
  if(e.kind==='photo') return showPhoto(e.id);
  if(e.kind==='pin') {
    const d=e.data, imported=!!e.source_id;
    modal(`${d.ref} · pin ${d.name||d.pin}`,`<div class="pin-connection"><span>Connected net</span><strong>${esc(d.net)}</strong><p>${imported?'Connection recorded in your boardview.':'Mapping recorded as checked by the technician.'}</p></div><dl class="evidence-facts"><dt>Component</dt><dd>${esc(d.ref)}</dd><dt>Pin label</dt><dd>${esc(d.name||d.pin)}</dd>${d.name&&d.name!==d.pin?`<dt>Boardview pin ID</dt><dd>${esc(d.pin)} <span class="muted">(numeric identifier in the file)</span></dd>`:''}<dt>Source</dt><dd>${esc(e.name||'Technician checked mapping')}</dd>${d.side?`<dt>Board side</dt><dd>${esc(d.side==='T'?'Top (T)':d.side==='B'?'Bottom (B)':d.side)}</dd>`:''}</dl><p class="${e.conflict?'warning':'modal-copy'}">${e.conflict?'Conflicting mappings: verify this pin before using it.':e.scope_confirmed?'Board and revision applicability has been checked.':'File applicability is unconfirmed: the board/revision has not been matched to a physical repair. You can still explore these reference files.'}</p><p class="modal-copy">This identifies an electrical connection. Expected voltage and circuit behavior come from the matching schematic and test conditions.</p>${d.basis?`<p class="small">Verification: ${esc(d.basis)}</p>`:''}<details class="source-details"><summary>Technical source details</summary><pre class="evidence-json">${esc(JSON.stringify(d,null,2))}</pre></details>`);
    return;
  }
  modal('Evidence · '+id,`<p class="modal-copy">${esc(e.name||'Recorded case observation')}${e.conflict?' · CONFLICT: check this mapping before use':''}</p><pre class="evidence-json">${esc(JSON.stringify(e.data,null,2))}</pre>`);
}
async function lookup(event) {
  event.preventDefault();const q=$('#lookup-query').value.trim();if(!q)return;
  try{
    const found=await api(`/api/lookup?case_id=${caseId()}&q=${encodeURIComponent(q)}`);found.forEach(e=>evidence[e.id]=e);
    const coverage=found[0]?.lookup_coverage;
    const row=e=>`<div class="lookup-row"><b>${esc(e.kind==='pin'?`${pinLabel(e.data)} → ${e.data.net}`:e.kind==='document'?documentLabel(e.data):e.name||e.kind)}</b>${e.kind==='pin'?`<p>${e.source_id?'Boardview connection':'Technician checked mapping'} · ${esc(e.conflict?'Conflicting mappings — verify before use':e.scope_confirmed?'Board scope checked':'File applicability unconfirmed')}</p>`:e.kind==='document'?`<p>${/^(?:schematic\s+)?(?:sheet|(?:pdf\s+)?page)\s+\d+$/i.test(q)?'Matching schematic page.':`Page text mentions ${esc(e.matched_reference||q)}. Open the drawing to inspect the circuit.`}</p><details class="source-details"><summary>Extracted text</summary><p>${esc(e.data.text||'No text excerpt available.')}</p></details>`:`<p>${esc(e.data.text||JSON.stringify(e.data))}</p>`}<button class="source-chip" data-evidence="${esc(e.id)}">${e.kind==='document'?'Open schematic page':e.kind==='pin'?'View pin details':'View evidence'}</button></div>`;
    const group=(title,items)=>items.length?`<section class="lookup-group"><h4>${title}</h4>${items.map(row).join('')}</section>`:'';
    $('#lookup-results').innerHTML=(coverage?.truncated?`<p class="muted small">Showing ${coverage.imported_pins_returned} of ${coverage.imported_pin_matches} imported pin matches and ${coverage.documents_returned} of ${coverage.document_matches} document pages. Narrow the search for more specific results.</p>`:'')+group('Pin connections',found.filter(e=>e.kind==='pin'))+group('Schematic references',found.filter(e=>e.kind==='document'))+group('Other evidence',found.filter(e=>!['pin','document'].includes(e.kind)))||'<p class="muted small">No matching evidence in this case. Check spelling, including +, − and #. Add a checked mapping if needed.</p>';
  }catch(e){notice(e.message);}
}
async function ask() {
  const text=$('#question').value.trim();if(!text){$('#question').focus();return;}
  try {draftSave();const id=caseId();const job=await api('/api/ask',{case_id:id,question:text,photo_ids:[...selectedPhotos],research:$('#research').checked,deep_analysis:$('#deep-analysis').checked});pendingQuestions.set(job.id,{caseId:id,text});notice('');await refresh();}
  catch(e){notice(e.message);}
}
async function download(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  try{
    if(b.dataset.case) await loadCase(b.dataset.case);
    if(b.dataset.dismiss){dismissedJobs.add(b.dataset.dismiss);render();}
    if(b.dataset.tab){tab=b.dataset.tab;render();}
    if(b.dataset.test) addReading(currentPlan().data.plan.tests[Number(b.dataset.test)]);
    if(b.dataset.evidence) await showEvidence(b.dataset.evidence);
    if(b.dataset.cancel){await api('/api/cancel',{id:b.dataset.cancel});await refresh();}
    if(b.dataset.retract){await api('/api/retract',{case_id:caseId(),event_id:b.dataset.retract});await refresh();notice('Entry retracted. The original remains in the timeline.');}
    if(b.dataset.importIssues){const source=current.sources.find(s=>s.id===b.dataset.importIssues);modal('Import details',`<p class="modal-copy">${esc(source.name)} · ${source.info.pin_count||0} pins imported. Excluded components have no accepted mapping; the netlist is incomplete. Check them in FlexBV before recording an individual verified mapping.</p>${source.info.issues.map(issue=>`<article class="panel"><h3>${esc(issue.ref||'Unnamed component')}</h3><p class="small">Lines ${issue.line_start}–${issue.line_end}</p><p>${esc(issue.reason)}</p></article>`).join('')}${source.info.issue_list_truncated?'<p>Additional issues are omitted from this view.</p>':''}`);}
    if(b.dataset.viewPhoto) await showPhoto(b.dataset.viewPhoto);
    if(b.dataset.viewPdf) await showPDF(b.dataset.viewPdf);
    if(b.dataset.openSource){await api('/api/open-source',{case_id:caseId(),source_id:b.dataset.openSource});notice('Opened in the Mac viewer.');}
    if(b.dataset.download){const s=current.sources.find(s=>s.id===b.dataset.download);await download(await binary(`/api/file?case_id=${caseId()}&id=${s.id}`),s.name);}
    if(b.dataset.scope){const s=current.sources.find(s=>s.id===b.dataset.scope);modal('Confirm file scope',`<form id="scope-form"><p class="modal-copy">File: ${esc(s.name)}<br>Current board: ${esc(current.board_id||'unknown')} / ${esc(current.revision||'unknown')}</p><label class="check"><input name="confirmed" type="checkbox" ${s.scope_confirmed?'checked':''}> I checked that this file matches this board and revision.</label>${footer('Save scope check')}</form>`,()=>formAction($('#scope-form'),async form=>{await api('/api/source-scope',{case_id:caseId(),source_id:s.id,confirmed:form.has('confirmed')});closeModal();await refresh();}));}
    if(b.dataset.retry){const s=current.sources.find(s=>s.id===b.dataset.retry);if(s.status==='partial'&&s.name.toLowerCase().endsWith('.pdf')){modal('OCR selected pages',`<form id="ocr-form"><p class="modal-copy">Pages without searchable text: ${esc((s.info.image_only_pages||[]).slice(0,30).join(', '))}. Choose up to 10 per request. Tesseract must be available on this Mac; if it is missing, the original page viewer still works.</p>${field('pages','Page numbers','','required placeholder="1, 2, 3"')}${footer('Run OCR')}</form>`,()=>formAction($('#ocr-form'),async form=>{const pages=String(form.get('pages')).split(',').map(x=>Number(x.trim()));await api('/api/retry-import',{case_id:caseId(),source_id:s.id,ocr_pages:pages});closeModal();await refresh();}));}else{await api('/api/retry-import',{case_id:caseId(),source_id:s.id});await refresh();}}
  }catch(e){notice(e.message);}
});
document.addEventListener('change',event=>{const id=event.target.dataset.photo;if(id){if(event.target.checked&&selectedPhotos.size>=2){event.target.checked=false;notice('Choose up to two photos for one question.');return;}event.target.checked?selectedPhotos.add(id):selectedPhotos.delete(id);}});
$('#new-case').onclick=()=>newCase();$('#welcome-new').onclick=()=>newCase();$('#new-reference').onclick=()=>newCase('reference');$('#start-from-reference').onclick=()=>newCase('repair',current);$('#settings-button').onclick=settings;$('#close-modal').onclick=closeModal;
$('#modal').addEventListener('close',()=>{if(!$('#modal').open)clearModalResources();});
$('#edit-case').onclick=editCase;
$('#case-search').oninput=render;$('#add-reading').onclick=()=>addReading();$('#add-note').onclick=addNote;$('#add-pin').onclick=addPin;$('#outcome').onclick=outcome;$('#upload-button').onclick=upload;
$('#deep-analysis').onchange=renderAnalysisHint;
$('#lookup-form').onsubmit=lookup;$('#ask').onclick=ask;$('#question').oninput=draftSave;
$('#question').onkeydown=event=>{if((event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();ask();}};
$('#export-case').onclick=async()=>{try{await download(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}),`${current.id}-repair.json`);}catch(e){notice(e.message);}};
$('#backup').onclick=async()=>{try{const result=await api('/api/backup',{});await download(await binary('/api/backup-file?name='+encodeURIComponent(result.filename)),result.filename);notice('Backup saved. It contains cases and original files; the API key is excluded.');}catch(e){notice(e.message);}};
$('#find-cases').onclick=async()=>{try{const found=await api(`/api/confirmed?case_id=${caseId()}&q=${encodeURIComponent(current.symptom)}`);modal('Confirmed repair references',found.map(c=>`<div class="panel"><h3>${esc(c.title)}</h3><span class="pill ${c.same_board_revision?'':'warn'}">${c.same_board_revision?'Same board / revision':'Different or unknown board / revision'}</span><p class="small form-gap">${esc(c.symptom)}</p><p class="small">Fix: ${esc(c.outcome.fix)}</p><p class="small muted">Verified by: ${esc(c.outcome.verification)}<br>Cause: ${esc(c.outcome.cause_status)}</p></div>`).join('')||'<p class="modal-copy">No relevant confirmed repairs yet. Record a fix and post-repair verification to build this reference set.</p>');}catch(e){notice(e.message);}};
$('#quit').onclick=async()=>{try{draftSave();await api('/api/quit',{});notice('BoardBrain has closed. Reopen it from the app icon.');}catch(e){notice(e.message);}};
window.addEventListener('beforeunload',draftSave);
(async()=>{await refresh();const previous=sessionStorage.getItem('bb-case');if(previous&&state?.cases.some(c=>c.id===previous))await loadCase(previous);else if(state?.cases.length===1)await loadCase(state.cases[0].id);setInterval(refresh,2200);})();
