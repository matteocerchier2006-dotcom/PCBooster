// PC Booster - logica dell'interfaccia
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const root = document.documentElement;

let api;
const state = { prefs: {}, system: null, gpus: null, junk: null, updates: null, startup: null, apps: [], appFilter: 'all', large: [], view: null };

// ---------- avvio: aspetta il ponte con Python ----------
async function ready() {
  api = window.pywebview.api;
  state.prefs = await api.get_prefs();
  applyPrefs();
  bindSettings();
  bindTray();
  go(state.prefs.start_page || 'home');
  loadHome();
  loadTweaks();
  initAppUpdate();
  api.get_game_mode().then(g => { $('#gameDot').hidden = !g.state; });
}
if (window.pywebview && window.pywebview.api) ready();
else window.addEventListener('pywebviewready', ready);

// ================= Utilità =================
let toastTimer;
function toast(msg, ok = true) {
  const t = $('#toast');
  t.textContent = msg; t.classList.toggle('err', !ok); t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 5000);
}
async function busy(btn, fn) {
  btn.disabled = true; btn.classList.add('busy');
  try { return await fn(); }
  catch (e) { toast('Qualcosa non ha funzionato: ' + e, false); }
  finally { btn.disabled = false; btn.classList.remove('busy'); }
}
function confirmBox(title, text, yesLabel = 'Conferma', danger = false) {
  const d = $('#confirm');
  $('#confirmTitle').textContent = title; $('#confirmText').textContent = text;
  const yes = $('#confirmYes'); yes.textContent = yesLabel; yes.className = 'btn ' + (danger ? 'danger' : 'primary');
  d.returnValue = ''; d.showModal();
  return new Promise(res => d.addEventListener('close', () => res(d.returnValue === 'yes'), { once: true }));
}
const fmtBytes = n => { if (n == null) return '—'; const u = ['B','KB','MB','GB','TB']; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i < 2 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i]; };
const fmtMB = mb => mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb ? mb + ' MB' : '—';
const level = p => p >= 90 ? 'bad' : p >= 75 ? 'warn' : '';
const plural = (n, one, many) => n === 1 ? one : many;
function bindSeg(el, value, onPick) {
  $$('button', el).forEach(b => {
    b.classList.toggle('on', b.dataset.v === value);
    b.onclick = () => { $$('button', el).forEach(x => x.classList.toggle('on', x === b)); onPick(b.dataset.v); };
  });
}

// ================= Aspetto e preferenze =================
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
darkQuery.addEventListener('change', () => state.prefs.theme === 'auto' && applyPrefs());
function applyPrefs() {
  const p = state.prefs;
  root.dataset.theme = p.theme === 'auto' ? (darkQuery.matches ? 'dark' : 'light') : (p.theme || 'dark');
  root.dataset.radius = p.radius || 'medium';
  root.dataset.size = p.font_size || 'normal';
  root.dataset.font = p.font || 'barlow';
  root.dataset.compact = p.compact_sidebar ? 'on' : 'off';
  root.dataset.anim = p.animations === false ? 'off' : 'on';
  root.dataset.glow = p.glow === false ? 'off' : 'on';
  const accent = p.accent || '#8b7bff';
  root.style.setProperty('--accent', accent);
  root.style.setProperty('--on-accent', luminance(accent) > 0.55 ? '#111318' : '#ffffff');
}
function luminance(hex) {
  const m = hex.replace('#', '').match(/.{2}/g); if (!m) return 0;
  const [r, g, b] = m.map(x => { const c = parseInt(x, 16) / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
let saveTimer;
function setPref(key, value) {
  state.prefs[key] = value; applyPrefs();
  clearTimeout(saveTimer); saveTimer = setTimeout(() => api.set_prefs(state.prefs), 300);
}
function bindSettings() {
  const p = state.prefs;
  bindSeg($('#themePick'), p.theme, v => setPref('theme', v));
  bindSeg($('#fontPick'), p.font, v => setPref('font', v));
  bindSeg($('#sizePick'), p.font_size, v => setPref('font_size', v));
  bindSeg($('#radiusPick'), p.radius, v => setPref('radius', v));
  const markAccent = () => {
    let preset = false;
    $$('#accentPick button').forEach(b => { const on = b.dataset.v.toLowerCase() === (state.prefs.accent || '').toLowerCase(); b.classList.toggle('on', on); preset ||= on; });
    $('#accentPick .custom').classList.toggle('on', !preset);
  };
  $$('#accentPick button').forEach(b => b.onclick = () => { setPref('accent', b.dataset.v); markAccent(); });
  $('#accentCustom').value = p.accent || '#8b7bff';
  $('#accentCustom').oninput = e => { setPref('accent', e.target.value); markAccent(); };
  markAccent();
  $('#startPick').value = p.start_page || 'home';
  $('#startPick').onchange = e => setPref('start_page', e.target.value);
  $$('[data-pref]').forEach(i => {
    i.checked = !!p[i.dataset.pref];
    i.onchange = async () => {
      if (i.dataset.pref === 'no_uac_prompt') {
        state.prefs.no_uac_prompt = i.checked;
        const r = await api.set_no_uac(i.checked); toast(r.msg, r.ok);
        if (!r.ok) { i.checked = !i.checked; state.prefs.no_uac_prompt = i.checked; }
        return;
      }
      setPref(i.dataset.pref, i.checked);
      if (i.dataset.pref === 'auto_update') toast('Aggiornamento automatico ' + (i.checked ? 'attivato.' : 'disattivato.'));
    };
  });
  $('#resetLook').onclick = async () => {
    state.prefs = await api.reset_prefs(); applyPrefs(); bindSettings(); toast('Aspetto predefinito ripristinato.');
  };
}

// ================= Navigazione =================
$$('.nav').forEach(b => b.addEventListener('click', () => go(b.dataset.view)));
function go(view) {
  if (!$('#view-' + view)) view = 'home';
  state.view = view;
  $$('.nav').forEach(n => n.classList.toggle('on', n.dataset.view === view));
  $$('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + view));
  $('#main').scrollTop = 0;
  if (view === 'gpu') { if (!state.gpus) loadGpu(); else { renderGpu(); startGpuLive(); } }
  if (view === 'updates') loadCpu();
  if (view === 'disk') loadDrives();
  if (view === 'startup') loadStartup();
  if (view === 'apps' && !state.apps.length) loadApps();
  if (view === 'settings') loadSettingsView();
  if (view === 'game') loadGame();
  if (view === 'security') loadSecurity();
  if (view === 'disk') loadDiskHealth();
}

// ================= Panoramica =================
async function loadHome() {
  const s = await api.get_system_info();
  state.system = s;
  $('#pcName').textContent = s.pc_name || 'Il tuo PC';
  $('#osLine').textContent = s.os + (s.uptime ? ` · acceso da ${s.uptime}` : '');
  const specs = $('#specs'); specs.innerHTML = '';
  [['Processore', s.cpu, `${s.cores} core · ${s.threads} thread`], ['Scheda video', s.gpu, ''],
   ['Memoria RAM', s.ram_gb + ' GB', s.ram_detail], ['Disco di sistema', `${s.disk_free_gb} GB liberi`, `su ${s.disk_total_gb} GB`]]
    .forEach(([k, v, sm]) => { const d = h('div', 'spec'); d.append(h('span', null, k), h('b', null, v)); if (sm) d.append(h('small', null, sm)); specs.append(d); });
  renderAdmin(s.admin);
  paintMeters(s);
  setInterval(async () => { if (state.view === 'home') paintMeters(await api.get_live_stats()); }, 3000);
}
function paintMeters(s) {
  const diskUsed = s.disk_total_gb ? Math.round(100 - s.disk_free_gb / s.disk_total_gb * 100) : 0;
  [['Cpu', s.cpu_load, s.cpu_load + '%'], ['Ram', s.ram_used, s.ram_used_gb ? `${s.ram_used_gb} GB · ${s.ram_used}%` : s.ram_used + '%'],
   ['Disk', diskUsed, `${s.disk_free_gb} GB liberi`]].forEach(([k, p, label]) => {
    $('#m' + k).textContent = label;
    const bar = $('#b' + k); bar.style.width = p + '%'; bar.className = level(p);
  });
}
function renderAdmin(isAdmin) {
  const a = $('#adminState'); a.innerHTML = '';
  if (isAdmin) return;
  a.append(h('b', null, 'Modalità limitata'), document.createTextNode('Alcune funzioni richiedono i permessi.'));
  const b = h('button', 'btn small', 'Riavvia come amministratore'); b.onclick = () => api.restart_as_admin(); a.append(b);
}

function setScore(score) {
  const el = $('#score');
  el.classList.remove('ok', 'warn', 'bad');
  if (score == null) { $('#scoreNum').textContent = '?'; $('#scoreLabel').textContent = 'Non analizzato'; $('#scoreArc').style.strokeDashoffset = 326.7; return; }
  el.classList.add(score >= 80 ? 'ok' : score >= 55 ? 'warn' : 'bad');
  $('#scoreNum').textContent = score;
  $('#scoreLabel').textContent = score >= 90 ? 'Ottimo' : score >= 80 ? 'Buono' : score >= 55 ? 'Da sistemare' : 'Serve una pulizia';
  $('#scoreArc').style.strokeDashoffset = 326.7 * (1 - score / 100);
}

$('#checkupBtn').addEventListener('click', e => busy(e.currentTarget, runCheckup));
async function runCheckup() {
  const list = $('#findingsList'); list.innerHTML = '';
  $('#findingsEmpty').hidden = true;
  setScore(null); $('#scoreLabel').textContent = 'Analisi in corso…';
  let penalty = 0;
  const steps = {
    gpu: addFinding('pending', 'Controllo il driver della scheda video…'),
    junk: addFinding('pending', 'Cerco file inutili…'),
    tw: addFinding('pending', 'Controllo le impostazioni per i giochi…'),
    st: addFinding('pending', 'Controllo i programmi all\'avvio…'),
    upd: addFinding('pending', 'Chiedo a Windows Update se ci sono driver e aggiornamenti…'),
    wg: addFinding('pending', 'Controllo se i tuoi programmi sono aggiornati…'),
    sec: addFinding('pending', 'Controllo antivirus e firewall…'),
    dh: addFinding('pending', 'Controllo la salute dei dischi…'),
  };

  const gpus = await api.get_gpu_info(); state.gpus = gpus;
  const main = gpus.find(g => (g.vendor === 'nvidia' || g.vendor === 'amd') && (g.kind === 'Dedicata' || gpus.length === 1));
  const dr = main ? await checkDriver(main, true) : null;
  const old = gpus.filter(g => g.status === 'warn' && g.kind === 'Dedicata');
  if (dr && dr.ok && dr.update) { penalty += 10; fillFinding(steps.gpu, 'warn', `Nuovo driver per ${main.name}: ${dr.version}`, `Tu hai il ${dr.installed}. I driver nuovi migliorano le prestazioni nei giochi recenti.`, 'Installa', () => go('gpu')); }
  else if (dr && dr.ok && !dr.unknown_installed) fillFinding(steps.gpu, 'ok', 'Hai l\'ultimo driver della scheda video', `${main.name} · ${dr.version}`);
  else if (old.length) { penalty += 10; fillFinding(steps.gpu, 'warn', `Driver di ${old[0].name} di ${old[0].age_days} giorni fa`, 'Probabilmente ne è uscito uno più nuovo, con miglioramenti per i giochi recenti.', 'Vai al driver', () => go('gpu')); }
  else fillFinding(steps.gpu, 'ok', 'Driver della scheda video recente', gpus.filter(g => g.kind === 'Dedicata').map(g => `${g.name} · ${g.driver_version}`).join(', ') || gpus.map(g => g.name).join(', '));

  const junk = await api.scan_junk(); state.junk = junk; renderJunk();
  const total = junk.items.filter(i => i.default).reduce((a, j) => a + (j.bytes || 0), 0);
  if (total > 1024 ** 3) { penalty += 15; fillFinding(steps.junk, 'warn', `${fmtBytes(total)} di file inutili`, 'Temporanei, cache e vecchi aggiornamenti che puoi eliminare senza rischi.', 'Pulisci', () => go('clean')); }
  else if (total > 300 * 1024 ** 2) { penalty += 6; fillFinding(steps.junk, 'warn', `${fmtBytes(total)} di file inutili`, 'Una pulizia veloce libera un po\' di spazio.', 'Pulisci', () => go('clean')); }
  else fillFinding(steps.junk, 'ok', 'Il PC è pulito', `Solo ${fmtBytes(total)} di file temporanei.`);

  const tw = await api.get_gpu_tweaks();
  const missing = [['game_mode', true, 'Modalità Gioco'], ['hags', true, 'pianificazione GPU'], ['high_perf', true, 'Prestazioni elevate'], ['game_dvr', false, 'registrazione in background spenta']]
    .filter(([k, want]) => !!tw[k] !== want).map(x => x[2]);
  if (missing.length) { penalty += missing.length * 3; fillFinding(steps.tw, 'warn', `${missing.length} ${plural(missing.length, 'ottimizzazione', 'ottimizzazioni')} per i giochi non ${plural(missing.length, 'attiva', 'attive')}`, 'Mancano: ' + missing.join(', ') + '.', 'Ottimizza', () => $('#optimizeBtn').click()); }
  else fillFinding(steps.tw, 'ok', 'Impostazioni per i giochi a posto', 'Tutte le ottimizzazioni sono attive.');

  const st = await api.list_startup(); state.startup = st;
  const heavy = st.filter(x => x.enabled && x.suggest === 'off');
  if (heavy.length) { penalty += Math.min(10, heavy.length * 2); fillFinding(steps.st, 'warn', `${heavy.length} ${plural(heavy.length, 'programma parte', 'programmi partono')} da soli all'accensione`, heavy.slice(0, 4).map(x => x.name).join(', ') + (heavy.length > 4 ? '…' : ''), 'Disattiva', () => go('startup')); }
  else fillFinding(steps.st, 'ok', 'Avvio leggero', 'Nessun programma inutile parte con Windows.');

  const s = state.system;
  if (s && s.disk_total_gb) {
    const free = s.disk_free_gb / s.disk_total_gb;
    if (free < 0.1) { penalty += 20; addFinding('bad', `Disco di sistema quasi pieno (${s.disk_free_gb} GB liberi)`, 'Windows rallenta quando il disco è pieno. Cerca i file grandi o disinstalla qualche gioco.', 'Libera spazio', () => go('disk')); }
    else if (free < 0.2) { penalty += 8; addFinding('warn', `Poco spazio sul disco di sistema (${s.disk_free_gb} GB liberi)`, 'Conviene liberare un po\' di spazio.', 'Libera spazio', () => go('disk')); }
  }

  const r = await api.check_updates();
  if (!r.ok) fillFinding(steps.upd, 'bad', 'Windows Update non ha risposto', r.msg);
  else {
    state.updates = r.updates; renderUpdates();
    const drv = r.updates.filter(u => u.category !== 'system').length;
    if (r.updates.length) { penalty += 8 + drv * 3; fillFinding(steps.upd, 'warn', `${r.updates.length} ${plural(r.updates.length, 'aggiornamento disponibile', 'aggiornamenti disponibili')}` + (drv ? `, di cui ${drv} driver` : ''), r.updates.slice(0, 2).map(u => u.title).join(' — '), 'Installa', () => go('updates')); }
    else fillFinding(steps.upd, 'ok', 'Windows e i driver sono aggiornati', 'Nessun aggiornamento in sospeso.');
  }
  const sec = await api.security_status(); state.sec = sec;
  const thr = (await api.security_threats()).filter(t => t.active);
  if (sec.ok || sec.other_av) {
    const avOn = sec.realtime || (sec.other_av || []).some(a => a.on);
    const fwOff = (sec.firewall || []).filter(f => !f.on).length;
    if (thr.length) { penalty += 25; fillFinding(steps.sec, 'bad', `${thr.length} ${plural(thr.length, 'minaccia trovata', 'minacce trovate')}`, thr.map(t => t.name).slice(0, 2).join(', '), 'Rimuovi', () => go('security')); }
    else if (!avOn) { penalty += 20; fillFinding(steps.sec, 'bad', 'La protezione antivirus è spenta', 'Il PC non è protetto dai virus in questo momento.', 'Attiva', () => go('security')); }
    else if (fwOff) { penalty += 10; fillFinding(steps.sec, 'bad', 'Il firewall è spento', 'Il firewall blocca gli accessi indesiderati da internet.', 'Attiva', () => go('security')); }
    else if (sec.realtime && sec.sig_age_days > 3) { penalty += 5; fillFinding(steps.sec, 'warn', `Definizioni dei virus vecchie di ${sec.sig_age_days} giorni`, 'Aggiornale per riconoscere i virus più nuovi.', 'Aggiorna', () => go('security')); }
    else fillFinding(steps.sec, 'ok', 'Il PC è protetto', sec.realtime ? 'Microsoft Defender attivo, definizioni aggiornate, firewall acceso.' : `Antivirus attivo: ${(sec.other_av || []).filter(a => a.on).map(a => a.name).join(', ')}.`);
  } else fillFinding(steps.sec, 'warn', 'Non riesco a leggere lo stato dell\'antivirus', sec.msg || '');
  setSecDot(sec, thr);

  const disks = await api.disk_health(); state.disks = disks;
  const badDisk = disks.find(d => d.status === 'bad'), warnDisk = disks.find(d => d.status === 'warn');
  if (badDisk) { penalty += 20; fillFinding(steps.dh, 'bad', `Il disco ${badDisk.name} ha un problema`, badDisk.note, 'Vedi', () => go('disk')); }
  else if (warnDisk) { penalty += 8; fillFinding(steps.dh, 'warn', `Attenzione al disco ${warnDisk.name}`, warnDisk.note, 'Vedi', () => go('disk')); }
  else fillFinding(steps.dh, 'ok', 'Dischi in salute', disks.map(d => `${d.name}${d.life != null ? ` · vita ${d.life}%` : ''}`).join(', '));

  const wg = await api.list_app_updates();
  if (!wg.ok) fillFinding(steps.wg, 'warn', 'Non riesco a controllare i programmi', wg.msg);
  else {
    state.wg = wg.apps; renderWinget();
    if (wg.apps.length) { penalty += Math.min(10, wg.apps.length * 2); fillFinding(steps.wg, 'warn', `${wg.apps.length} ${plural(wg.apps.length, 'programma da aggiornare', 'programmi da aggiornare')}`, wg.apps.slice(0, 4).map(a => a.name).join(', ') + (wg.apps.length > 4 ? '…' : ''), 'Aggiorna', () => go('updates')); }
    else fillFinding(steps.wg, 'ok', 'Tutti i programmi sono aggiornati', 'Nessuna versione nuova da installare.');
  }
  setScore(Math.max(0, Math.min(100, 100 - penalty)));
}
function addFinding(lvl, title, text, action, fn) {
  const li = h('li', 'finding ' + lvl); $('#findingsList').append(li); fillFinding(li, lvl, title, text, action, fn); return li;
}
function fillFinding(li, lvl, title, text, action, fn) {
  li.className = 'finding ' + lvl; li.innerHTML = '';
  const body = h('div'); body.append(h('b', null, title)); if (text) body.append(h('span', null, text));
  li.append(h('i', 'dot'), body);
  if (action) { const b = h('button', 'btn small', action); b.onclick = fn; li.append(b); } else li.append(h('i'));
}

$('#optimizeBtn').addEventListener('click', async e => {
  const btn = e.currentTarget;
  const rp = state.prefs.restore_before_optimize !== false;
  const ok = await confirmBox('Ottimizzare il PC per i giochi?',
    (rp ? 'Prima creo un punto di ripristino, poi ' : '') + 'attivo Modalità Gioco, pianificazione GPU e Prestazioni elevate, spengo la registrazione in background e tolgo dall\'avvio i programmi non necessari. Puoi annullare tutto dalle sezioni Scheda video e Avvio automatico.', 'Ottimizza');
  if (!ok) return;
  busy(btn, async () => {
    const r1 = await api.optimize_all(rp);
    const r2 = await api.disable_suggested_startup();
    toast(r1.msg + ' ' + r2.msg, r1.ok);
    loadTweaks(); state.startup = null;
    if ($('#findingsList').children.length) runCheckup();
  });
});

// ================= Pulizia =================
$('#scanBtn').addEventListener('click', e => busy(e.currentTarget, async () => { $('#cleanResult').innerHTML = ''; state.junk = await api.scan_junk(); renderJunk(); }));
const openGroups = new Set(['windows', 'browser']);
function renderJunk() {
  const box = $('#cleanGroups'); box.innerHTML = '';
  const { categories, items } = state.junk;
  categories.forEach(cat => {
    const list = items.filter(i => i.cat === cat.id);
    if (!list.length) return;
    const g = h('div', 'group' + (openGroups.has(cat.id) ? ' open' : ''));
    const head = h('div', 'group-head');
    const gck = h('input', 'ck'); gck.type = 'checkbox'; gck.onclick = e => e.stopPropagation();
    const title = h('h3', null, cat.label); title.append(h('small', null, `${list.length} ${plural(list.length, 'voce', 'voci')}`));
    const gsize = h('span', 'gsize');
    const chev = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); chev.setAttribute('viewBox', '0 0 24 24'); chev.classList.add('chev'); chev.innerHTML = '<path d="M6 9l6 6 6-6"/>';
    head.append(gck, title, gsize, chev);
    head.onclick = () => { g.classList.toggle('open'); g.classList.contains('open') ? openGroups.add(cat.id) : openGroups.delete(cat.id); };
    const body = h('div', 'group-body');
    list.forEach(it => {
      const row = h('label', 'item' + (it.bytes === 0 && it.special !== 'dns' ? ' zero' : ''));
      const ck = h('input', 'ck'); ck.type = 'checkbox'; ck.value = it.id; ck.dataset.cat = cat.id;
      ck.checked = it.default && (it.bytes !== 0 || it.special === 'dns');
      ck.disabled = it.bytes === 0 && it.special !== 'dns';
      ck.addEventListener('change', () => { syncGroup(); updateCleanTotal(); });
      const mid = h('div'); const name = h('b', null, it.label);
      if (it.busy) { const t = h('span', 'tag warn', 'aperto'); t.style.display = 'inline-block'; t.title = 'Chiudilo per pulire tutto'; name.append(t); }
      mid.append(name, h('span', null, it.desc));
      row.append(ck, mid, h('span', 'isize', it.special === 'dns' ? '' : it.size));
      body.append(row);
    });
    const syncGroup = () => {
      const cks = $$('input.ck', body).filter(c => !c.disabled);
      const on = cks.filter(c => c.checked).length;
      gck.checked = on > 0 && on === cks.length; gck.indeterminate = on > 0 && on < cks.length; gck.disabled = !cks.length;
      const sel = list.filter(i => cks.some(c => c.checked && c.value === i.id)).reduce((a, i) => a + (i.bytes || 0), 0);
      const all = list.reduce((a, i) => a + (i.bytes || 0), 0);
      gsize.textContent = on ? fmtBytes(sel) : fmtBytes(all);
      gsize.style.color = on ? '' : 'var(--muted)';
    };
    gck.onchange = () => { $$('input.ck', body).filter(c => !c.disabled).forEach(c => c.checked = gck.checked); syncGroup(); updateCleanTotal(); };
    g.append(head, body); box.append(g); syncGroup();
  });
  $('#cleanBar').hidden = false;
  updateCleanTotal();
}
function selectedJunk() { return $$('#cleanGroups .item input.ck:checked').map(i => i.value); }
function updateCleanTotal() {
  const sel = selectedJunk();
  const tot = state.junk.items.filter(j => sel.includes(j.id)).reduce((a, j) => a + (j.bytes || 0), 0);
  $('#cleanTotal').textContent = fmtBytes(tot);
}
$('#cleanBtn').addEventListener('click', async e => {
  const btn = e.currentTarget;
  const ids = selectedJunk();
  if (!ids.length) return toast('Seleziona almeno una voce.', false);
  const warn = [];
  if (ids.includes('recycle')) warn.push('i file nel Cestino verranno eliminati per sempre');
  if (ids.includes('winold')) warn.push('non potrai più tornare alla versione precedente di Windows');
  if (ids.includes('dism')) warn.push('la pulizia dei componenti di Windows può richiedere 10-20 minuti');
  if (warn.length && !await confirmBox('Sei sicuro?', 'Attenzione: ' + warn.join('; ') + '.', 'Pulisci', ids.includes('winold'))) return;
  const busyItems = state.junk.items.filter(i => ids.includes(i.id) && i.busy).map(i => i.label);
  if (busyItems.length) toast(`${busyItems.join(', ')} ${plural(busyItems.length, 'è aperto', 'sono aperti')}: chiudi per pulire tutto, intanto pulisco il resto.`);
  busy(btn, async () => {
    const r = await api.clean_junk(ids);
    const res = $('#cleanResult'); res.innerHTML = '';
    const card = h('div', 'result'); card.append(h('b', null, r.freed), h('span', null, r.msg.replace(/^Liberati [^.]+\.\s*/, '') || 'Fatto: il PC è più leggero.'));
    res.append(card);
    state.junk = await api.scan_junk(); renderJunk();
  });
});

// ================= Spazio su disco =================
let drivesLoaded = false;
async function loadDrives() {
  if (drivesLoaded) return; drivesLoaded = true;
  const drives = await api.get_drives();
  const box = $('#drives'); box.innerHTML = '';
  drives.forEach(d => {
    const c = h('div', 'drive');
    const top = h('div', 'drive-top'); top.append(h('b', null, d.letter), h('span', 'tag acc', d.kind)); if (d.label) top.append(h('span', 'tag muted', d.label));
    const bar = h('i'), em = h('em', level(d.used_pct)); em.style.width = d.used_pct + '%'; bar.append(em);
    c.append(top, bar, h('span', 'meta', `${d.free_gb} GB liberi su ${d.total_gb} GB` + (d.model ? ` · ${d.model}` : '')));
    box.append(c);
  });
}
$('#largeBtn').addEventListener('click', e => busy(e.currentTarget, async () => {
  $('#largeList').innerHTML = '<p class="empty">Cerco nelle tue cartelle…</p>';
  state.large = await api.find_large_files(+$('#largeMin').value); renderLarge();
}));
function renderLarge() {
  const box = $('#largeList'); box.innerHTML = '';
  if (!state.large.length) { box.append(h('p', 'empty', 'Nessun file così grande nelle tue cartelle.')); return; }
  const tot = state.large.reduce((a, f) => a + f.bytes, 0);
  box.append(h('p', 'empty', `${state.large.length} file, ${fmtBytes(tot)} in totale.`));
  const list = h('div', 'list'); list.style.marginTop = '10px';
  state.large.forEach(f => {
    const row = h('div', 'lrow');
    const ext = (f.name.split('.').pop() || '').slice(0, 4).toUpperCase();
    const ico = h('div', 'ico', ext); ico.style.fontSize = '.72em';
    const mid = h('div'); mid.append(h('b', null, f.name), h('span', 'meta', f.folder));
    const show = h('button', 'btn small', 'Mostra'); show.onclick = () => api.reveal_file(f.id);
    const del = h('button', 'btn small', 'Nel Cestino');
    del.onclick = async () => {
      if (!await confirmBox(`Spostare ${f.name} nel Cestino?`, `Libera ${f.size}. Potrai recuperarlo dal Cestino finché non lo svuoti.`, 'Sposta nel Cestino')) return;
      const r = await api.recycle_file(f.id); toast(r.msg, r.ok); if (r.ok) { row.classList.add('gone'); del.disabled = show.disabled = true; }
    };
    const acts = h('div', 'row-actions'); acts.append(show, del);
    row.append(ico, mid, h('div', 'num', f.size), h('div', 'date', f.date), acts);
    list.append(row);
  });
  box.append(list);
}

// ================= Scheda video =================
$('#gpuRefresh').addEventListener('click', e => busy(e.currentTarget, loadGpu));
async function loadGpu() { state.gpus = await api.get_gpu_info(); renderGpu(); startGpuLive(); }
function renderGpu() {
  const box = $('#gpuList'); box.innerHTML = '';
  if (!state.gpus.length) { box.append(h('p', 'empty', 'Nessuna scheda video rilevata.')); return; }
  state.gpus.forEach((g, idx) => {
    const c = h('div', 'gpu');
    const head = h('div', 'gpu-head'); head.append(h('h3', null, g.name), h('span', 'tag ' + (g.kind === 'Dedicata' ? 'acc' : 'muted'), g.kind));
    const det = h('div', 'details');
    const cell = (k, v, sm) => { const d = h('div'); d.append(h('span', null, k), h('b', null, v || 'n.d.')); if (sm) d.append(h('small', null, sm)); det.append(d); };
    cell('Versione driver', g.driver_version, g.driver_raw && g.driver_raw !== g.driver_version ? 'Windows: ' + g.driver_raw : '');
    cell('Data del driver', g.driver_date ? new Date(g.driver_date).toLocaleDateString('it-IT', { day: 'numeric', month: 'long', year: 'numeric' }) : '', g.age_days != null ? `${g.age_days} giorni fa` : '');
    cell('Memoria video', g.vram);
    if (g.display) cell('Schermo', g.display);
    c.append(head, det);
    if (g.live) { const live = h('div', 'live'); live.id = 'gpuLive'; c.append(live); }
    const online = (g.vendor === 'nvidia' || g.vendor === 'amd') && (g.kind === 'Dedicata' || state.gpus.length === 1);
    if (online) { const d = h('div', 'drv'); d.dataset.gpu = g.name; c.append(d); paintDriver(g, d); }
    const v = h('div', 'verdict'), p = h('p');
    if (!online) {
      if (g.status === 'warn') { p.append(h('b', null, 'Driver di più di 6 mesi fa. ')); p.append('Probabilmente ne è uscito uno più nuovo: scaricalo dal produttore.'); }
      else if (g.status === 'ok') { p.append(h('b', null, 'Driver recente. ')); p.append('Controlla ogni tanto: escono versioni nuove per i giochi appena usciti.'); }
      else p.textContent = 'Non riesco a leggere la data del driver.';
    } else p.textContent = 'Puoi anche usare l\'app ufficiale del produttore.';
    v.append(p);
    if (g.vendor !== 'altro' && (g.kind === 'Dedicata' || idx === 0)) {
      const appBtn = h('button', 'btn', 'Apri app ' + ({ nvidia: 'NVIDIA', amd: 'AMD', intel: 'Intel' })[g.vendor]);
      appBtn.onclick = async () => { const r = await api.open_vendor_app(g.vendor); toast(r.msg, r.ok); if (!r.ok) api.open_url(g.download_page); };
      const site = h('button', 'btn', 'Sito del produttore'); site.onclick = () => api.open_url(g.download_page);
      v.append(appBtn, site);
    }
    c.append(v); box.append(c);
  });
}

// ---------- ultimo driver (NVIDIA e AMD) ----------
state.drv = {};
async function checkDriver(g, force = false) {
  if (!force && state.drv[g.name]) return state.drv[g.name];
  const r = await api.check_gpu_driver(g.name);
  state.drv[g.name] = r; return r;
}
async function paintDriver(g, box) {
  box.innerHTML = ''; box.append(h('p', null, 'Controllo online l\'ultima versione del driver…'));
  const r = await checkDriver(g);
  box.innerHTML = '';
  const p = h('p');
  if (!r.ok) {
    p.append(r.msg || 'Non riesco a controllare online.');
    const again = h('button', 'btn small', 'Riprova'); again.onclick = () => { delete state.drv[g.name]; paintDriver(g, box); };
    box.append(p, again); return;
  }
  const when = r.date ? ` del ${fmtDate(r.date)}` : '';
  if (r.update) {
    p.append(h('b', null, `È uscito il driver ${r.version}${when}. `), `Tu hai il ${r.installed}.` + (r.size ? ` Download: ${r.size}.` : ''));
    const dl = h('button', 'btn primary', 'Scarica e installa');
    dl.onclick = () => startDriverDownload(g, r, box);
    box.append(p, dl);
  } else if (r.unknown_installed) {
    p.append(h('b', null, `Ultima versione: ${r.version}${when}. `), 'Non riesco a leggere la tua versione per confrontarla.');
    const dl = h('button', 'btn', 'Scarica e installa'); dl.onclick = () => startDriverDownload(g, r, box);
    box.append(p, dl);
  } else {
    box.classList.add('okbox');
    p.append(h('b', null, 'Hai già l\'ultimo driver. '), `Versione ${r.version}${when}.`);
    box.append(p);
  }
}
function fmtDate(s) {
  const d = new Date(s); if (isNaN(d)) return s;
  return d.toLocaleDateString('it-IT', { day: 'numeric', month: 'long', year: 'numeric' });
}
async function startDriverDownload(g, r, box) {
  const ok = await confirmBox(`Installare il driver ${r.version}?`, 'Lo scarico dal sito ufficiale ' + (g.vendor === 'nvidia' ? 'NVIDIA' : 'AMD') +
    ' e poi si apre l\'installazione del produttore: segui le sue schermate. Durante l\'installazione lo schermo può diventare nero per qualche secondo: è normale.', 'Scarica');
  if (!ok) return;
  const s = await api.download_driver(r.url, g.vendor);
  if (!s.ok) return toast(s.msg, false);
  box.innerHTML = '';
  const p = h('p', null, 'Download in corso…'), bar = h('div', 'bar'), em = h('em'); bar.append(em);
  box.append(p, bar);
  const t = setInterval(async () => {
    const st = await api.driver_progress();
    if (st.status === 'downloading') {
      const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
      em.style.width = pct + '%';
      p.textContent = `Download in corso: ${fmtBytes(st.done)}` + (st.total ? ` di ${fmtBytes(st.total)} (${pct}%)` : '');
    } else {
      clearInterval(t);
      if (st.status === 'launched') { em.style.width = '100%'; p.textContent = st.msg; toast(st.msg); }
      else if (st.status === 'error') { p.textContent = st.msg; toast(st.msg, false); }
    }
  }, 700);
}
let gpuTimer;
function startGpuLive() {
  clearInterval(gpuTimer);
  if (!state.gpus || !state.gpus.some(g => g.live)) return;
  const tick = async () => {
    if (state.view !== 'gpu') return clearInterval(gpuTimer);
    const box = $('#gpuLive'); if (!box) return;
    const d = await api.get_gpu_live(); if (!d.ok) return;
    box.innerHTML = '';
    [['Temperatura', d.temp, '°C'], ['Utilizzo', d.load, '%'], ['Memoria usata', d.mem_used, d.mem_total ? `/ ${d.mem_total} GB` : 'GB'], ['Consumo', d.power, 'W']]
      .forEach(([k, val, unit]) => { const c = h('div'); const b = h('b', null, val == null ? '—' : val); if (val != null) b.append(h('small', null, unit)); c.append(h('span', null, k), b); box.append(c); });
  };
  tick(); gpuTimer = setInterval(tick, 2000);
}
async function loadTweaks() {
  const t = await api.get_gpu_tweaks();
  $$('[data-tweak]').forEach(i => { i.checked = !!t[i.dataset.tweak]; });
  $('#planName').textContent = 'Piano attuale: ' + t.power_plan;
}
$$('[data-tweak]').forEach(i => i.addEventListener('change', async () => {
  const r = await api.set_gpu_tweak(i.dataset.tweak, i.checked);
  toast(r.msg, r.ok); if (!r.ok) i.checked = !i.checked; loadTweaks();
}));

// ================= Processore e aggiornamenti =================
let cpuLoaded = false;
async function loadCpu() {
  if (cpuLoaded) return; cpuLoaded = true;
  const c = await api.get_cpu_info();
  const box = $('#cpuBox'); box.innerHTML = '';
  [['Processore', c.name], ['Frequenza base', c.base_mhz ? (c.base_mhz / 1000).toFixed(2) + ' GHz' : ''], ['Core / thread', `${c.cores} / ${c.threads}`],
   ['Cache L3', c.cache], ['Socket', c.socket], ['Scheda madre', c.board], ['BIOS', [c.bios_version, c.bios].filter(Boolean).join(' · ')]]
    .filter(([, v]) => v).forEach(([k, v]) => { const d = h('div'); d.append(h('span', null, k), h('b', null, v)); box.append(d); });
  if (c.chipset_page) { const b = $('#chipsetBtn'); b.hidden = false; b.onclick = () => api.open_url(c.chipset_page); }
}
$('#updCheck').addEventListener('click', e => busy(e.currentTarget, async () => {
  $('#updArea').innerHTML = '<p class="empty">Ricerca su Windows Update in corso…</p>';
  const r = await api.check_updates();
  if (!r.ok) { $('#updArea').innerHTML = ''; $('#updArea').append(h('p', 'empty', r.msg)); return; }
  state.updates = r.updates; renderUpdates();
}));
function renderUpdates() {
  const area = $('#updArea'); area.innerHTML = '';
  if (!state.updates.length) { area.append(h('p', 'ok-line', 'Tutto aggiornato: nessun driver o aggiornamento in sospeso.')); return; }
  [['gpu', 'Scheda video'], ['cpu', 'Processore e chipset'], ['system', 'Windows e altri driver']].forEach(([cat, label]) => {
    const items = state.updates.filter(u => u.category === cat); if (!items.length) return;
    const g = h('div', 'updgroup'), t = h('h3', null, label); t.append(h('small', null, `${items.length} ${plural(items.length, 'aggiornamento', 'aggiornamenti')}`)); g.append(t);
    items.forEach(u => { const row = h('label', 'upd'), cb = h('input', 'ck'); cb.type = 'checkbox'; cb.checked = true; cb.value = u.id; row.append(cb, h('span', null, u.title), h('em', null, u.size)); g.append(row); });
    area.append(g);
  });
  const bar = h('div', 'updbar'), btn = h('button', 'btn primary', 'Installa selezionati');
  bar.append(h('span', null, 'Alcuni aggiornamenti chiedono di riavviare il PC.'), btn);
  btn.onclick = () => busy(btn, async () => {
    const ids = $$('.upd input:checked').map(i => i.value);
    if (!ids.length) return toast('Seleziona almeno un aggiornamento.', false);
    toast('Download e installazione in corso: può volerci un po\'.');
    const r = await api.install_updates(ids); toast(r.msg, r.ok);
  });
  area.append(bar);
}

// ================= Avvio automatico =================
async function loadStartup() {
  const list = $('#startupList');
  if (!state.startup) { list.innerHTML = '<p class="empty">Leggo i programmi all\'avvio…</p>'; state.startup = await api.list_startup(); }
  renderStartup();
}
function renderStartup() {
  const list = $('#startupList'); list.innerHTML = '';
  if (!state.startup.length) { list.append(h('p', 'empty', 'Nessun programma parte all\'avvio.')); return; }
  state.startup.forEach(it => {
    const row = h('label', 'lrow' + (it.enabled ? ' accent' : ' off')); row.style.gridTemplateColumns = '38px 1fr auto auto'; row.style.cursor = 'pointer'; row.style.position = 'relative';
    const txt = h('div'); txt.append(h('b', null, it.name), h('code', null, it.command)); txt.title = it.where;
    let tag;
    if (!it.enabled) tag = h('span', 'tag muted', 'Disattivato');
    else if (it.suggest === 'off') tag = h('span', 'tag warn', 'Consigliato disattivare');
    else if (it.suggest === 'keep') tag = h('span', 'tag ok', 'Meglio lasciarlo');
    else tag = h('span');
    const cb = h('input'); cb.type = 'checkbox'; cb.checked = it.enabled; cb.style.position = 'absolute'; cb.style.opacity = 0;
    cb.addEventListener('change', async () => {
      const r = await api.set_startup(it.id, cb.checked); toast(r.msg, r.ok);
      if (r.ok) it.enabled = cb.checked; renderStartup();
    });
    const sw = h('span'); sw.style.display = 'inline-flex'; sw.append(cb, h('i', 'sw'));
    row.append(h('div', 'ico', it.name.trim().charAt(0).toUpperCase()), txt, tag, sw);
    list.append(row);
  });
}
$('#startupSuggest').addEventListener('click', e => busy(e.currentTarget, async () => {
  const r = await api.disable_suggested_startup(); toast(r.msg, r.ok);
  state.startup = await api.list_startup(); renderStartup();
}));

// ================= App e giochi =================
$('#appsRefresh').addEventListener('click', e => busy(e.currentTarget, loadApps));
$('#appSearch').addEventListener('input', renderApps);
bindSeg($('#appFilter'), 'all', v => { state.appFilter = v; renderApps(); });
async function loadApps() { $('#appList').innerHTML = '<p class="empty">Leggo i programmi installati…</p>'; state.apps = await api.list_apps(); renderApps(); }
function renderApps() {
  const q = $('#appSearch').value.trim().toLowerCase();
  const list = $('#appList'); list.innerHTML = '';
  const shown = state.apps.filter(a => (state.appFilter === 'all' || (state.appFilter === 'games') === a.game) &&
    (!q || a.name.toLowerCase().includes(q) || (a.publisher || '').toLowerCase().includes(q)));
  if (!shown.length) { list.append(h('p', 'empty', q ? `Niente che corrisponda a "${q}".` : 'Nessun elemento.')); return; }
  shown.forEach(a => {
    const row = h('div', 'lrow' + (a.game ? ' game' : ''));
    const name = h('div'); name.append(h('b', null, a.name), h('span', 'meta', (a.game ? 'Gioco · ' : '') + (a.publisher || 'Editore sconosciuto')));
    const btn = h('button', 'btn small', 'Disinstalla');
    btn.onclick = async () => {
      if (!await confirmBox(`Disinstallare ${a.name}?`, 'Si aprirà il programma di disinstallazione ufficiale. I salvataggi nel cloud non vengono toccati, quelli locali potrebbero sparire.', 'Disinstalla', true)) return;
      const r = await api.uninstall_app(a.id); toast(r.msg, r.ok); if (r.ok) row.classList.add('gone');
    };
    row.append(h('div', 'ico', a.name.trim().charAt(0).toUpperCase()), name, h('div', 'num', fmtMB(a.size_mb)), h('div', 'date', a.date || ''), btn);
    list.append(row);
  });
}

// ================= Impostazioni =================
async function loadSettingsView() {
  const info = await api.get_app_info();
  $('#verLine').textContent = `Versione ${info.version}` + (info.repo ? ` · github.com/${info.repo}` : '');
  renderAppUpdate(info.update, info);
  const ac = await api.get_auto_clean();
  bindSeg($('#autoCleanPick'), ac.freq, async v => { const r = await api.set_auto_clean(v); toast(r.msg, r.ok); if (!r.ok) loadSettingsView(); });
  if (!ac.available) $('#autoCleanHint').textContent = 'Disponibile nella versione scaricata da GitHub.';
}
function renderAppUpdate(u, info) {
  const box = $('#appUpdStatus'); box.innerHTML = '';
  const p = h('p');
  const status = u ? u.status : 'idle';
  $('#updDot').hidden = status !== 'ready';
  if (status === 'ready') {
    p.append(h('b', null, `Versione ${u.latest} pronta. `), 'Si installa quando chiudi PC Booster: la prossima volta che lo apri sarà già aggiornato. Oppure premi il pulsante e si riapre da solo.');
    box.append(p);
    const b = h('button', 'btn primary small', 'Aggiorna ora'); b.onclick = () => busy(b, async () => { const r = await api.apply_update_now(); toast(r.msg, r.ok); }); box.append(b);
    if (u.notes) box.append(h('p', null, 'Novità: ' + u.notes));
  } else if (status === 'checking') { p.append('Controllo se ci sono versioni nuove…'); box.append(p); }
  else if (status === 'none') { p.append(h('b', null, 'Hai l\'ultima versione. ')); box.append(p); }
  else if (status === 'error' || status === 'source') { p.append(u.msg || ''); box.append(p); }
  else { p.append(info && !info.configured ? 'Gli aggiornamenti funzionano nella versione scaricata da GitHub.' : 'Nessun controllo fatto finora.'); box.append(p); }
}
async function initAppUpdate() {
  const info = await api.get_app_info();
  if (info.just_updated) toast(`PC Booster aggiornato alla versione ${info.version}.`);
  if (state.prefs.auto_update === false) return;
  const u = await api.prepare_update();
  $('#updDot').hidden = u.status !== 'ready';
  if (state.view === 'settings') renderAppUpdate(u);
}
$('#appUpdCheck').addEventListener('click', e => busy(e.currentTarget, async () => {
  renderAppUpdate({ status: 'checking' });
  const u = await api.prepare_update(); renderAppUpdate(u, await api.get_app_info());
}));
$('#rpCreate').addEventListener('click', e => busy(e.currentTarget, async () => { const r = await api.create_restore_point(); toast(r.msg, r.ok); }));
$('#rpOpen').addEventListener('click', () => api.open_system_restore());


// ================= Programmi da aggiornare (winget) =================
$('#wgCheck').addEventListener('click', e => busy(e.currentTarget, async () => {
  $('#wgArea').innerHTML = '<p class="empty">Controllo i programmi installati… può volerci un minuto.</p>';
  const r = await api.list_app_updates();
  if (!r.ok) {
    $('#wgArea').innerHTML = ''; const p = h('p', 'empty', r.msg); $('#wgArea').append(p);
    if (r.missing) { const b = h('button', 'btn small', 'Apri il Microsoft Store'); b.style.marginTop = '10px'; b.onclick = () => api.open_winget_store(); $('#wgArea').append(b); }
    return;
  }
  state.wg = r.apps; renderWinget();
}));
function renderWinget() {
  const area = $('#wgArea'); area.innerHTML = '';
  if (!state.wg || !state.wg.length) { area.append(h('p', 'ok-line', 'Tutti i programmi sono aggiornati.')); return; }
  const list = h('div');
  state.wg.forEach(a => {
    const row = h('label', 'wgrow'); row.dataset.id = a.id;
    const cb = h('input', 'ck'); cb.type = 'checkbox'; cb.checked = true; cb.value = a.id;
    const name = h('div'); name.append(h('b', null, a.name), h('span', 'meta', a.id));
    const ver = h('span', 'ver'); ver.append(a.version + ' → ', h('b', null, a.available));
    const st = h('span', 'st');
    row.append(cb, name, ver, st); list.append(row);
  });
  area.append(list);
  const bar = h('div', 'updbar'); bar.style.marginTop = '12px';
  const btn = h('button', 'btn primary', 'Aggiorna selezionati');
  bar.append(h('span', null, 'Chiudi i programmi prima di aggiornarli.'), btn);
  btn.onclick = () => busy(btn, async () => {
    const rows = $$('.wgrow', area).filter(r => $('input', r).checked && !r.dataset.done);
    if (!rows.length) return toast('Seleziona almeno un programma.', false);
    let ok = 0;
    for (const r of rows) {
      const st = $('.st', r); st.innerHTML = ''; st.append(h('span', 'tag acc', 'In corso…'));
      const res = await api.upgrade_app(r.dataset.id);
      st.innerHTML = '';
      if (res.ok) { ok++; r.dataset.done = '1'; $('input', r).checked = false; $('input', r).disabled = true; st.append(h('span', 'tag ok', 'Aggiornato')); }
      else { const t = h('span', 'tag warn', 'Non riuscito'); t.title = res.msg; st.append(t); }
    }
    toast(`${ok} su ${rows.length} ${plural(rows.length, 'programma aggiornato', 'programmi aggiornati')}.`, ok > 0);
  });
  area.append(bar);
}

// ================= Modalità partita =================
let game = null;
async function loadGame() {
  game = await api.get_game_mode();
  paintGame();
}
function paintGame() {
  const on = !!game.state;
  $('#gameDot').hidden = !on;
  $('#gameHero').classList.toggle('on', on);
  $('#gameState').textContent = on ? 'Attiva' : 'Spenta';
  $('#gameSetup').hidden = on;
  const btn = $('#gameBtn');
  const text = $('#gameText'); text.innerHTML = '';
  $$('.game-stats', $('#gameHero')).forEach(x => x.remove());
  if (on) {
    $('#gameTitle').textContent = 'Buona partita!';
    text.textContent = `Attiva dalle ${game.state.since}. Quando hai finito premi Termina: rimetto tutto com'era.`;
    const stats = h('div', 'game-stats');
    const stat = (v, l) => { const d = h('div'); d.append(h('b', null, v), h('span', null, l)); stats.append(d); };
    stat(fmtBytes(game.state.ram_freed || 0), 'RAM liberata');
    stat(String((game.state.closed || []).length), 'app chiuse');
    stat(game.state.prev_plan ? 'Sì' : 'No', 'prestazioni elevate');
    text.after(stats);
    btn.textContent = 'Termina modalità partita'; btn.className = 'btn big';
  } else {
    $('#gameTitle').textContent = 'Pronto a giocare?';
    text.textContent = 'Scegli cosa fare qui sotto e premi il pulsante. Avviala prima di aprire il gioco.';
    btn.textContent = 'Avvia modalità partita'; btn.className = 'btn primary big';
    const opts = game.opts || {};
    $$('[data-gm]').forEach(i => { i.checked = opts[i.dataset.gm] !== false; });
    renderGameApps();
  }
}
function renderGameApps() {
  const list = $('#gameApps'); list.innerHTML = '';
  if (!game.apps.length) { list.append(h('p', 'empty', 'Nessuna app pesante aperta: sei già pronto.')); return; }
  game.apps.forEach(a => {
    const row = h('label', 'lrow'); row.style.gridTemplateColumns = 'auto 38px 1fr auto'; row.style.cursor = 'pointer';
    const cb = h('input', 'ck'); cb.type = 'checkbox'; cb.value = a.exe; cb.checked = a.suggest;
    const name = h('div'); name.append(h('b', null, a.label), h('span', 'meta', a.exe));
    row.append(cb, h('div', 'ico', a.label.charAt(0).toUpperCase()), name, h('div', 'num', fmtMB(a.mem_mb)));
    list.append(row);
  });
}
$('#gameRefresh').addEventListener('click', e => busy(e.currentTarget, loadGame));
$('#gameBtn').addEventListener('click', e => busy(e.currentTarget, async () => {
  if (game && game.state) {
    const r = await api.stop_game_mode(); toast(r.msg, r.ok);
  } else {
    const opts = {}; $$('[data-gm]').forEach(i => opts[i.dataset.gm] = i.checked);
    const names = $$('#gameApps input:checked').map(i => i.value);
    const st = await api.start_game_mode(opts, names); toast(st.msg);
  }
  await loadGame();
}));

// ================= Icona vicino all'orologio =================
function bindTray() {
  $$('[data-tray]').forEach(i => {
    const k = i.dataset.tray;
    i.checked = k === 'start_with_windows' ? !!state.prefs[k] : state.prefs[k] !== false;
    i.onchange = async () => {
      const r = await api.set_tray(k, i.checked); toast(r.msg, r.ok);
      if (!r.ok) i.checked = !i.checked; else state.prefs[k] = i.checked;
    };
  });
}
$('#trayCheck').addEventListener('click', async () => { const r = await api.tray_check_now(); toast(r.msg, r.ok); });
window.onTrayChange = async () => {
  const g = await api.get_game_mode(); $('#gameDot').hidden = !g.state;
  if (state.view === 'game') loadGame();
};

// ================= Sicurezza =================
const SHIELD = '<svg viewBox="0 0 48 54"><path class="body" d="M24 3l19 7v14c0 12-8 22-19 27C13 46 5 36 5 24V10z"/><path class="mark" d="M15 27l6 6 12-13"/></svg>';
const SHIELD_X = '<svg viewBox="0 0 48 54"><path class="body" d="M24 3l19 7v14c0 12-8 22-19 27C13 46 5 36 5 24V10z"/><path class="mark" d="M24 16v14M24 37v1"/></svg>';
function setSecDot(sec, active) { $('#secDot').hidden = !(active.length || (sec && !sec.realtime && !(sec.other_av || []).some(a => a.on))); }
async function loadSecurity() {
  const [st, th] = await Promise.all([api.security_status(), api.security_threats()]);
  state.sec = st; renderSecHero(st, th); renderThreats(th); setSecDot(st, th.filter(t => t.active));
  const p = await api.security_progress(); if (p.status === 'running') showScan(p);
}
function renderSecHero(st, th) {
  const box = $('#secHero'); box.innerHTML = ''; box.className = 'sec-hero';
  if (!st.ok && !(st.other_av || []).length) { box.append(h('p', 'empty', st.msg || 'Non riesco a leggere lo stato dell\'antivirus.')); return; }
  const active = th.filter(t => t.active);
  const other = (st.other_av || []).filter(a => a.on);
  const avOn = st.realtime || other.length;
  const fwOff = (st.firewall || []).filter(f => !f.on);
  const lvl = active.length || !avOn || fwOff.length ? 'bad' : (st.realtime && st.sig_age_days > 3) || (st.quick_age_days == null || st.quick_age_days > 14) ? 'warn' : 'ok';
  box.classList.add(lvl);
  const sh = h('div', 'shield'); sh.innerHTML = lvl === 'bad' ? SHIELD_X : SHIELD;
  const right = h('div');
  right.append(h('h2', null, lvl === 'ok' ? 'Il PC è protetto' : lvl === 'warn' ? 'Protetto, ma c\'è qualcosa da fare' : 'Il PC è a rischio'));
  const checks = h('div', 'checks');
  const row = (l, html, btnText, fn) => {
    const r = h('div', 'check ' + l), sp = h('span'); sp.innerHTML = html; r.append(h('i', 'dot'), sp);
    if (btnText) { const b = h('button', 'btn small', btnText); b.onclick = () => busy(b, fn); r.append(b); } else r.append(h('i'));
    checks.append(r);
  };
  if (other.length && !st.realtime) row('ok', `<b>Antivirus:</b> ${other.map(a => a.name).join(', ')}. Defender fa i controlli che chiedi tu.`);
  else row(st.realtime ? 'ok' : 'bad', st.realtime ? '<b>Microsoft Defender</b> attivo, con protezione in tempo reale.' : '<b>Protezione in tempo reale spenta.</b>',
    st.realtime ? null : 'Attiva', async () => { const r = await api.security_realtime(); toast(r.msg, r.ok); loadSecurity(); });
  if (st.sig_age_days != null) row(st.sig_age_days > 3 ? 'warn' : 'ok', `<b>Definizioni dei virus:</b> ${st.sig_age_days === 0 ? 'aggiornate oggi' : `di ${st.sig_age_days} ${plural(st.sig_age_days, 'giorno', 'giorni')} fa`}${st.sig_updated ? ` (${st.sig_updated})` : ''}.`,
    st.sig_age_days > 3 ? 'Aggiorna' : null, updateDefs);
  const q = st.quick_age_days;
  row(q == null || q > 14 ? 'warn' : 'ok', `<b>Ultimo controllo:</b> ${q == null ? 'mai fatto' : q === 0 ? 'oggi' : `${q} ${plural(q, 'giorno', 'giorni')} fa`}.`, q == null || q > 14 ? 'Controlla ora' : null, () => startScan('quick'));
  row(fwOff.length ? 'bad' : 'ok', fwOff.length ? `<b>Firewall spento</b> sulle reti: ${fwOff.map(f => f.name).join(', ')}.` : '<b>Firewall</b> attivo su tutte le reti.',
    fwOff.length ? 'Attiva' : null, async () => { const r = await api.security_firewall(); toast(r.msg, r.ok); loadSecurity(); });
  if (active.length) row('bad', `<b>${active.length} ${plural(active.length, 'minaccia attiva', 'minacce attive')}</b>: rimuovile qui sotto.`);
  right.append(checks);
  box.append(sh, right);
}
function renderThreats(list) {
  const box = $('#threatList'); box.innerHTML = '';
  const rm = $('#secRemove'); const active = list.filter(t => t.active); rm.hidden = !active.length;
  if (!list.length) { box.append(h('p', 'ok-line', 'Nessuna minaccia trovata.')); return; }
  list.forEach(t => {
    const r = h('div', 'threat'), left = h('div'), name = h('b', null, t.name);
    left.append(name, h('span', 'meta', `Pericolo: ${t.severity}${t.when ? ` · trovata il ${t.when}` : ''}${t.ran ? ' · era stata avviata' : ''}`));
    t.files.forEach(f => left.append(h('span', 'meta', f)));
    r.append(left, h('span', 'tag ' + (t.active ? 'warn' : 'ok'), t.active ? 'Da rimuovere' : 'Rimossa'));
    box.append(r);
  });
}
$('#secRemove').addEventListener('click', e => busy(e.currentTarget, async () => {
  const r = await api.security_remove(); toast(r.msg, r.ok); loadSecurity();
}));
async function updateDefs() { toast('Aggiorno le definizioni dei virus…'); const r = await api.security_update(); toast(r.msg, r.ok); loadSecurity(); }
$('#secUpdate').addEventListener('click', e => busy(e.currentTarget, updateDefs));
$('#secOpen').addEventListener('click', () => api.open_windows_security());
$$('.scan-opt').forEach(b => b.addEventListener('click', () => startScan(b.dataset.scan)));
let scanTimer;
async function startScan(kind) {
  if (kind === 'offline') {
    if (!await confirmBox('Controllo offline?', 'Il PC si riavvia subito e fa un controllo di circa 15 minuti prima che parta Windows. Salva il lavoro aperto prima di continuare.', 'Riavvia e controlla', true)) return;
    const r = await api.security_offline(); toast(r.msg, r.ok); return;
  }
  const r = await api.security_scan(kind);
  if (!r.ok) return toast(r.msg, false);
  showScan({ status: 'running', kind, elapsed: 0 });
}
function showScan(p) {
  const box = $('#scanBox'); box.hidden = false;
  $$('.scan-opt').forEach(b => b.disabled = true);
  const paint = s => {
    box.innerHTML = '';
    const name = s.kind === 'full' ? 'Controllo completo' : 'Controllo veloce';
    if (s.status === 'running') {
      const m = Math.floor(s.elapsed / 60), sec = s.elapsed % 60;
      const p = h('p'); p.append(h('b', null, `${name} in corso… `), h('span', null, `${m} min ${String(sec).padStart(2, '0')} s. Puoi usare il PC e anche cambiare pagina.`));
      const c = h('button', 'btn small', 'Annulla'); c.onclick = async () => { const r = await api.security_cancel(); toast(r.msg); };
      box.append(h('div', 'spinner'), p, c);
    } else if (s.status === 'done') {
      const p = h('p');
      if (s.found) p.append(h('b', null, `${name} finito: ${s.found} ${plural(s.found, 'minaccia trovata', 'minacce trovate')}. `), h('span', null, 'Rimuovile dall\'elenco qui sotto.'));
      else p.append(h('b', null, `${name} finito: nessuna minaccia. `), h('span', null, `Durata: ${Math.max(1, Math.round(s.seconds / 60))} min.`));
      box.append(p);
    } else { box.hidden = true; }
  };
  paint(p);
  clearInterval(scanTimer);
  scanTimer = setInterval(async () => {
    const s = await api.security_progress(); paint(s);
    if (s.status !== 'running') { clearInterval(scanTimer); $$('.scan-opt').forEach(b => b.disabled = false); if (s.status === 'done') { toast(s.found ? 'Trovate delle minacce: controlla l\'elenco.' : 'Controllo finito: nessuna minaccia.', !s.found); loadSecurity(); } }
  }, 1000);
}

// ================= Salute dei dischi =================
async function loadDiskHealth(force = false) {
  if (state.disks && !force) return renderDiskHealth();
  $('#diskHealth').innerHTML = '<p class="empty">Leggo lo stato dei dischi…</p>';
  state.disks = await api.disk_health(); renderDiskHealth();
}
function renderDiskHealth() {
  const box = $('#diskHealth'); box.innerHTML = '';
  if (!state.disks.length) { box.append(h('p', 'empty', 'Non riesco a leggere i dischi.')); return; }
  const wrap = h('div', 'dhealth');
  state.disks.forEach(d => {
    const c = h('div', 'disk ' + d.status);
    const nm = h('div', 'disk-name'); nm.append(h('b', null, d.name), h('span', 'tag acc', d.kind)); if (d.letters) nm.append(h('span', 'tag muted', d.letters));
    const label = { ok: 'In salute', warn: 'Attenzione', bad: 'Problema' }[d.status];
    c.append(nm, h('span', 'tag ' + ({ ok: 'ok', warn: 'warn', bad: 'warn' })[d.status], label), h('p', null, d.note));
    const det = h('div', 'details');
    const cell = (k, v, extra) => { const x = h('div'); x.append(h('span', null, k), h('b', null, v)); if (extra) x.append(extra); det.append(x); };
    cell('Capacità', d.size_gb >= 1000 ? (d.size_gb / 1024).toFixed(1) + ' TB' : d.size_gb + ' GB');
    if (d.life != null) { const bar = h('i', 'lifebar'), em = h('em', d.life < 20 ? 'bad' : d.life < 40 ? 'warn' : ''); em.style.width = d.life + '%'; bar.append(em); cell('Vita residua', d.life + '%', bar); }
    cell('Temperatura', d.temp ? d.temp + ' °C' : 'n.d.');
    if (d.hours != null) cell('Ore di accensione', `${Number(d.hours).toLocaleString('it-IT')} h`, h('small', null, `circa ${Math.round(d.hours / 24)} giorni`));
    if (d.errors) cell('Errori di lettura/scrittura', String(d.errors));
    c.append(det); wrap.append(c);
  });
  box.append(wrap);
}
$('#healthRefresh').addEventListener('click', e => busy(e.currentTarget, () => loadDiskHealth(true)));