// PC Booster - logica dell'interfaccia
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let api;
const state = { system: null, gpus: null, updates: null, junk: null, apps: [], filter: 'all' };

// ---------- avvio: aspetta il ponte con Python ----------
function ready() {
  api = window.pywebview.api;
  loadHome();
  loadTweaks();
  initAppUpdates();
}
if (window.pywebview && window.pywebview.api) ready();
else window.addEventListener('pywebviewready', ready);

// ---------- navigazione ----------
$$('.nav').forEach(b => b.addEventListener('click', () => go(b.dataset.view)));
function go(view) {
  $$('.nav').forEach(n => n.classList.toggle('on', n.dataset.view === view));
  $$('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + view));
  if (view === 'gpu' && !state.gpus) loadGpu();
  if (view === 'updates') loadCpu();
  if (view === 'apps' && !state.apps.length) loadApps();
  if (view === 'startup') loadStartup();
}

// ---------- utilità ----------
let toastTimer;
function toast(msg, ok = true) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('err', !ok);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 4500);
}
async function busy(btn, fn) {
  btn.disabled = true; btn.classList.add('busy');
  try { return await fn(); }
  catch (e) { toast('Qualcosa non ha funzionato: ' + e, false); }
  finally { btn.disabled = false; btn.classList.remove('busy'); }
}
function confirmBox(title, text, yesLabel = 'Conferma') {
  const d = $('#confirm');
  $('#confirmTitle').textContent = title;
  $('#confirmText').textContent = text;
  $('#confirmYes').textContent = yesLabel;
  d.showModal();
  return new Promise(res => d.addEventListener('close', () => res(d.returnValue === 'yes'), { once: true }));
}
const fmtBytes = n => { const u = ['B','KB','MB','GB','TB']; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i < 2 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i]; };
const fmtMB = mb => mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb ? mb + ' MB' : '—';
const level = p => p >= 85 ? 'hot' : p >= 65 ? 'warm' : '';

// ---------- panoramica ----------
async function loadHome() {
  const s = await api.get_system_info();
  state.system = s;
  $('#pcName').textContent = s.pc_name || 'Il tuo PC';
  $('#osLine').textContent = s.os;
  const specs = $('#specs'); specs.innerHTML = '';
  [['Processore', s.cpu], ['Scheda video', s.gpu], ['Memoria', s.ram_gb + ' GB'], ['Core / thread', `${s.cores} / ${s.threads}`]]
    .forEach(([k, v]) => { const d = h('div', 'spec'); d.append(h('span', null, k), h('b', null, v)); specs.append(d); });
  renderAdmin(s.admin);
  paintMeters(s);
  setInterval(async () => paintMeters(await api.get_live_stats()), 3000);
}
function paintMeters(s) {
  const diskUsed = s.disk_total_gb ? Math.round(100 - s.disk_free_gb / s.disk_total_gb * 100) : 0;
  [['Cpu', s.cpu_load, s.cpu_load + '%'], ['Ram', s.ram_used, s.ram_used + '%'], ['Disk', diskUsed, `${s.disk_free_gb} GB liberi`]]
    .forEach(([k, p, label]) => {
      $('#m' + k).textContent = label;
      const bar = $('#b' + k); bar.style.width = p + '%'; bar.className = level(p);
    });
}
function renderAdmin(isAdmin) {
  const a = $('#adminState'); a.innerHTML = '';
  if (isAdmin) { a.append(h('b', null, 'Amministratore'), document.createTextNode('Tutte le funzioni sono attive.')); return; }
  a.append(h('b', null, 'Modalità limitata'), document.createTextNode('Driver, aggiornamenti e pulizia di sistema richiedono i permessi.'));
  const b = h('button', 'btn small', 'Riavvia come amministratore');
  b.onclick = () => api.restart_as_admin();
  a.append(b);
}

$('#checkupBtn').addEventListener('click', e => busy(e.currentTarget, runCheckup));
async function runCheckup() {
  const list = $('#findingsList'); list.innerHTML = '';
  $('#findingsEmpty').hidden = true;
  const steps = {
    gpu: addFinding('pending', 'Controllo il driver della scheda video…'),
    junk: addFinding('pending', 'Cerco file inutili…'),
    upd: addFinding('pending', 'Chiedo a Windows Update se ci sono driver e aggiornamenti…'),
  };

  const gpus = await api.get_gpu_info(); state.gpus = gpus; renderGpu();
  const old = gpus.filter(g => g.status === 'old');
  if (old.length) fillFinding(steps.gpu, 'warm', `Driver di ${old[0].name} vecchio di ${old[0].age_days} giorni`,
      'Probabilmente c\'è una versione più nuova, con miglioramenti per i giochi recenti.', 'Vai al driver', () => go('gpu'));
  else fillFinding(steps.gpu, 'cool', 'Driver della scheda video recente', gpus.map(g => `${g.name} · ${g.driver_version}`).join(', '));

  const junk = await api.scan_junk(); state.junk = junk; renderJunk();
  const total = junk.reduce((a, j) => a + j.bytes, 0);
  if (total > 500 * 1024 ** 2) fillFinding(steps.junk, 'warm', `${fmtBytes(total)} di file inutili`, 'Temporanei, cache e cestino che puoi eliminare senza rischi.', 'Pulisci', () => go('clean'));
  else fillFinding(steps.junk, 'cool', 'Poco da pulire', `Solo ${fmtBytes(total)} di file temporanei.`);

  const s = state.system;
  if (s && s.disk_total_gb && s.disk_free_gb / s.disk_total_gb < .12)
    addFinding('hot', `Spazio su disco quasi finito (${s.disk_free_gb} GB liberi)`, 'Disinstalla qualche gioco che non usi più.', 'Vedi giochi', () => { go('apps'); setFilter('games'); });

  const tw = await api.get_gpu_tweaks();
  const missing = [['game_mode', true, 'Modalità Gioco'], ['hags', true, 'pianificazione GPU'], ['high_perf', true, 'Prestazioni elevate'], ['game_dvr', false, 'registrazione in background spenta']]
    .filter(([k, want]) => !!tw[k] !== want).map(x => x[2]);
  if (missing.length) addFinding('warm', `${missing.length} ottimizzazioni per i giochi non attive`, 'Mancano: ' + missing.join(', ') + '.', 'Ottimizza tutto', () => $('#optimizeBtn').click());

  const st = await api.list_startup(); state.startup = st;
  const heavy = st.filter(x => x.enabled && x.suggest === 'off');
  if (heavy.length) addFinding('warm', `${heavy.length} programmi partono da soli all'accensione`,
      heavy.slice(0, 4).map(x => x.name).join(', ') + (heavy.length > 4 ? '…' : ''), 'Disattiva', () => go('startup'));

  const r = await api.check_updates();
  if (!r.ok) { fillFinding(steps.upd, 'hot', 'Windows Update non ha risposto', r.msg); return; }
  state.updates = r.updates; renderUpdates();
  const drv = r.updates.filter(u => u.category !== 'system').length;
  if (r.updates.length) fillFinding(steps.upd, drv ? 'warm' : 'cool',
      `${r.updates.length} aggiornamenti disponibili` + (drv ? `, di cui ${drv} driver` : ''),
      r.updates.slice(0, 2).map(u => u.title).join(' — '), 'Installa', () => go('updates'));
  else fillFinding(steps.upd, 'cool', 'Windows e i driver sono aggiornati', 'Nessun aggiornamento in sospeso.');
}
function addFinding(lvl, title, text, action, fn) {
  const li = h('li', 'finding ' + lvl);
  $('#findingsList').append(li);
  fillFinding(li, lvl, title, text, action, fn);
  return li;
}
function fillFinding(li, lvl, title, text, action, fn) {
  li.className = 'finding ' + lvl; li.innerHTML = '';
  const body = h('div'); body.append(h('b', null, title)); if (text) body.append(h('span', null, text));
  li.append(h('i', 'dot'), body);
  if (action) { const b = h('button', 'btn small', action); b.onclick = fn; li.append(b); } else li.append(h('i'));
}

// ---------- scheda video ----------
$('#gpuRefresh').addEventListener('click', e => busy(e.currentTarget, loadGpu));
async function loadGpu() { state.gpus = await api.get_gpu_info(); renderGpu(); }
function renderGpu() {
  const box = $('#gpuList'); box.innerHTML = '';
  if (!state.gpus.length) { box.append(h('p', 'empty', 'Nessuna scheda video rilevata.')); return; }
  state.gpus.forEach(g => {
    const c = h('div', 'gpu' + (g.status === 'old' ? ' warm' : ''));
    const head = h('div'); head.append(h('h3', null, g.name));
    const dl = h('dl');
    [['Versione driver', g.driver_version || '?'], ['Data driver', g.driver_date || '?'], ['Memoria video', g.vram_gb ? g.vram_gb + ' GB' : 'n.d.']]
      .forEach(([k, v]) => dl.append(h('dt', null, k), h('dd', null, v)));
    head.append(dl);
    c.append(head);
    const v = h('div', 'verdict'), p = h('p');
    if (g.status === 'old') { p.append(h('b', null, `Driver di ${g.age_days} giorni fa. `)); p.append('Conviene scaricare l\'ultima versione dal produttore.'); }
    else if (g.status === 'ok') { p.append(h('b', null, 'Driver recente. ')); p.append('Controlla comunque ogni tanto: escono versioni nuove per i giochi appena usciti.'); }
    else p.textContent = 'Non riesco a leggere la data del driver.';
    v.append(p);
    if (g.vendor !== 'altro') {
      const appBtn = h('button', 'btn', 'Apri app ' + ({ nvidia: 'NVIDIA', amd: 'AMD', intel: 'Intel' })[g.vendor]);
      appBtn.onclick = async () => { const r = await api.open_vendor_app(g.vendor); toast(r.msg, r.ok); if (!r.ok) api.open_url(g.download_page); };
      const site = h('button', 'btn primary', 'Scarica l\'ultimo driver');
      site.onclick = () => api.open_url(g.download_page);
      v.append(appBtn, site);
    }
    c.append(v);
    box.append(c);
  });
}
async function loadTweaks() {
  const t = await api.get_gpu_tweaks();
  $$('[data-tweak]').forEach(i => { i.checked = !!t[i.dataset.tweak]; });
  $('#planName').textContent = 'Piano attuale: ' + t.power_plan;
}
$$('[data-tweak]').forEach(i => i.addEventListener('change', async () => {
  const r = await api.set_gpu_tweak(i.dataset.tweak, i.checked);
  toast(r.msg, r.ok);
  if (!r.ok) i.checked = !i.checked;
  loadTweaks();
}));

// ---------- processore e aggiornamenti ----------
let cpuLoaded = false;
async function loadCpu() {
  if (cpuLoaded) return; cpuLoaded = true;
  const c = await api.get_cpu_info();
  const box = $('#cpuBox'); box.innerHTML = '';
  [['Processore', c.name], ['Frequenza max', c.max_mhz ? (c.max_mhz / 1000).toFixed(2) + ' GHz' : '?'], ['Core / thread', `${c.cores} / ${c.threads}`], ['BIOS', `${c.bios_version || ''} ${c.bios || ''}`.trim() || '?']]
    .forEach(([k, v]) => { const d = h('div'); d.append(h('span', null, k), h('b', null, v)); box.append(d); });
  if (c.chipset_page) {
    const b = h('button', 'btn link', 'Driver chipset dal produttore');
    b.onclick = () => api.open_url(c.chipset_page);
    box.append(b);
  }
  if (state.updates) renderUpdates();
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
  const groups = [['gpu', 'Scheda video'], ['cpu', 'Processore e chipset'], ['system', 'Windows e altri driver']];
  groups.forEach(([cat, label]) => {
    const items = state.updates.filter(u => u.category === cat);
    if (!items.length) return;
    const g = h('div', 'updgroup'), t = h('h3', null, label);
    t.append(h('small', null, items.length === 1 ? '1 aggiornamento' : items.length + ' aggiornamenti'));
    g.append(t);
    items.forEach(u => {
      const row = h('label', 'upd'), cb = h('input'); cb.type = 'checkbox'; cb.checked = true; cb.value = u.id;
      row.append(cb, h('span', null, u.title), h('em', null, u.size));
      g.append(row);
    });
    area.append(g);
  });
  const bar = h('div', 'updbar'), btn = h('button', 'btn primary', 'Installa selezionati');
  bar.append(h('span', null, 'Alcuni aggiornamenti chiedono di riavviare il PC.'), btn);
  btn.onclick = () => busy(btn, async () => {
    const ids = $$('.upd input:checked').map(i => i.value);
    if (!ids.length) return toast('Seleziona almeno un aggiornamento.', false);
    toast('Download e installazione in corso: può volerci un po\'.');
    const r = await api.install_updates(ids);
    toast(r.msg, r.ok);
  });
  area.append(bar);
}

// ---------- pulizia ----------
$('#scanBtn').addEventListener('click', e => busy(e.currentTarget, async () => { state.junk = await api.scan_junk(); renderJunk(); }));
function renderJunk() {
  const list = $('#junkList'); list.innerHTML = '';
  const max = Math.max(...state.junk.map(j => j.bytes), 1);
  state.junk.forEach(j => {
    const row = h('label', 'jrow' + (j.bytes ? '' : ' zero')), cb = h('input');
    cb.type = 'checkbox'; cb.value = j.id; cb.checked = j.bytes > 0 && j.id !== 'recycle'; cb.disabled = !j.bytes;
    cb.addEventListener('change', updateCleanTotal);
    const mid = h('div'); mid.append(h('span', null, j.label));
    const bar = h('div', 'bar'), em = h('em'); em.style.width = (j.bytes / max * 100) + '%'; bar.append(em); mid.append(bar);
    row.append(cb, mid, h('b', null, j.size));
    list.append(row);
  });
  $('#cleanBar').hidden = false;
  updateCleanTotal();
}
function updateCleanTotal() {
  const sel = $$('.jrow input:checked').map(i => i.value);
  const tot = state.junk.filter(j => sel.includes(j.id)).reduce((a, j) => a + j.bytes, 0);
  $('#cleanTotal').textContent = fmtBytes(tot);
}
$('#cleanBtn').addEventListener('click', async e => {
  const btn = e.currentTarget;
  const ids = $$('.jrow input:checked').map(i => i.value);
  if (!ids.length) return toast('Seleziona almeno una voce.', false);
  if (ids.includes('recycle') && !await confirmBox('Svuotare il cestino?', 'I file nel cestino verranno eliminati per sempre.', 'Svuota e pulisci')) return;
  busy(btn, async () => {
    const r = await api.clean_junk(ids);
    toast(r.msg, r.ok);
    state.junk = await api.scan_junk(); renderJunk();
  });
});

// ---------- app e giochi ----------
$('#appsRefresh').addEventListener('click', e => busy(e.currentTarget, loadApps));
$('#appSearch').addEventListener('input', renderApps);
$$('.seg button').forEach(b => b.addEventListener('click', () => setFilter(b.dataset.filter)));
function setFilter(f) { state.filter = f; $$('.seg button').forEach(b => b.classList.toggle('on', b.dataset.filter === f)); renderApps(); }
async function loadApps() {
  $('#appList').innerHTML = '<p class="empty" style="padding:16px 4px">Leggo i programmi installati…</p>';
  state.apps = await api.list_apps(); renderApps();
}
function renderApps() {
  const q = $('#appSearch').value.trim().toLowerCase();
  const list = $('#appList'); list.innerHTML = '';
  const shown = state.apps.filter(a =>
    (state.filter === 'all' || (state.filter === 'games') === a.game) &&
    (!q || a.name.toLowerCase().includes(q) || (a.publisher || '').toLowerCase().includes(q)));
  if (!shown.length) { list.append(h('p', 'empty', q ? `Niente che corrisponda a "${q}".` : 'Nessun elemento.')); list.lastChild.style.padding = '16px 4px'; return; }
  shown.forEach(a => {
    const row = h('div', 'app' + (a.game ? ' game' : ''));
    const name = h('div'); name.append(h('b', null, a.name), h('span', null, (a.game ? 'Gioco · ' : '') + (a.publisher || 'Editore sconosciuto')));
    const btn = h('button', 'btn small', 'Disinstalla');
    btn.onclick = async () => {
      if (!await confirmBox(`Disinstallare ${a.name}?`, 'Si aprirà il programma di disinstallazione ufficiale. I salvataggi nel cloud non vengono toccati, quelli locali potrebbero sparire.', 'Disinstalla')) return;
      const r = await api.uninstall_app(a.id);
      toast(r.msg, r.ok);
      if (r.ok) row.classList.add('gone');
    };
    row.append(h('div', 'ico', a.name.trim().charAt(0).toUpperCase()), name, h('div', 'size', fmtMB(a.size_mb)), h('div', 'date', a.date || ''), btn);
    list.append(row);
  });
}

// ---------- ottimizza tutto ----------
$('#optimizeBtn').addEventListener('click', async e => {
  const btn = e.currentTarget;
  const ok = await confirmBox('Ottimizzare il PC per i giochi?',
    'Attivo Modalità Gioco, pianificazione GPU hardware e piano Prestazioni elevate, spengo la registrazione in background della Game Bar e tolgo dall\'avvio automatico launcher e app non necessarie. Puoi annullare tutto dalle sezioni Scheda video e Avvio automatico.',
    'Ottimizza');
  if (!ok) return;
  busy(btn, async () => {
    const r1 = await api.optimize_all();
    const r2 = await api.disable_suggested_startup();
    toast(r1.msg + ' ' + r2.msg, r1.ok);
    loadTweaks();
    state.startup = null;
    const list = $('#findingsList');
    if (list.children.length) runCheckup();
  });
});

// ---------- avvio automatico ----------
async function loadStartup() {
  const list = $('#startupList');
  if (!state.startup) {
    list.innerHTML = '<p class="empty" style="padding:16px 4px">Leggo i programmi all\'avvio…</p>';
    state.startup = await api.list_startup();
  }
  renderStartup();
}
function renderStartup() {
  const list = $('#startupList'); list.innerHTML = '';
  if (!state.startup.length) { list.append(h('p', 'empty', 'Nessun programma parte all\'avvio.')); return; }
  state.startup.forEach(it => {
    const row = h('label', 'srow' + (it.enabled ? '' : ' off'));
    const txt = h('div'); txt.append(h('b', null, it.name), h('code', null, it.command)); txt.title = it.where;
    let tag;
    if (!it.enabled) tag = h('span', 'tag done', 'Disattivato');
    else if (it.suggest === 'off') tag = h('span', 'tag off', 'Consigliato disattivare');
    else if (it.suggest === 'keep') tag = h('span', 'tag keep', 'Meglio lasciarlo');
    else tag = h('span');
    const cb = h('input'); cb.type = 'checkbox'; cb.checked = it.enabled;
    cb.addEventListener('change', async () => {
      const r = await api.set_startup(it.id, cb.checked);
      toast(r.msg, r.ok);
      if (r.ok) it.enabled = cb.checked; else cb.checked = !cb.checked;
      renderStartup();
    });
    row.append(txt, tag, cb, h('i', 'sw'));
    list.append(row);
  });
}
$('#startupSuggest').addEventListener('click', e => busy(e.currentTarget, async () => {
  const r = await api.disable_suggested_startup();
  toast(r.msg, r.ok);
  state.startup = await api.list_startup(); renderStartup();
}));

// ---------- aggiornamento dell'app ----------
let appUpdate = null;
async function initAppUpdates() {
  const info = await api.get_app_info();
  $('#verLine').textContent = `Versione installata: ${info.version}`;
  $('#autoUpd').checked = info.auto_update;
  $('#repoLine').textContent = info.configured ? `Repository: ${info.repo}` : 'Disponibile solo nella versione scaricata da GitHub.';
  if (!info.configured || !info.auto_update) return;
  const r = await api.check_app_update();
  if (!r.available) return;
  appUpdate = r;
  if (info.frozen) installAppUpdate(true);
  else showBanner(`Versione ${r.latest} disponibile`, r.notes || 'Scaricala da GitHub per avere le novità.', false);
}
function showBanner(title, text, withButton = true) {
  $('#updBannerTitle').textContent = title;
  $('#updBannerText').textContent = text;
  $('#updBannerBtn').hidden = !withButton;
  $('#updBanner').hidden = false;
}
async function installAppUpdate(auto) {
  showBanner(`Aggiornamento alla versione ${appUpdate.latest}`, auto ? 'Lo sto scaricando: l\'app si riapre da sola tra poco.' : 'Download in corso…', false);
  const r = await api.install_app_update(appUpdate.url, appUpdate.size);
  if (!r.ok) showBanner('Aggiornamento non riuscito', r.msg, true);
  else toast(r.msg);
}
$('#updBannerBtn').addEventListener('click', () => appUpdate && installAppUpdate(false));
$('#autoUpd').addEventListener('change', async e => { const r = await api.set_auto_update(e.target.checked); toast(r.msg, r.ok); });
$('#appUpdCheck').addEventListener('click', e => busy(e.currentTarget, async () => {
  const r = await api.check_app_update();
  $('#appUpdResult').textContent = r.msg;
  if (r.available) { appUpdate = r; showBanner(`Versione ${r.latest} disponibile`, r.notes || 'Contiene le ultime modifiche.', true); }
}));
