// PC Booster - lingue.
// L'app è scritta in italiano. Per le altre lingue c'è un dizionario (es. lang-en.js) e questo file
// traduce i testi mentre compaiono sulla pagina: pulsanti, messaggi, avvisi, anche quelli che arrivano
// dal programma in Python. Un testo che manca nel dizionario resta semplicemente in italiano.
const I18N = (() => {
  let lang = 'it';
  const exact = new Map();
  let patterns = [];
  const done = new WeakMap();            // testo già tradotto per ogni nodo (evita di ritradurre)
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  // "Liberati {0}." → /^Liberati (.+?)\.$/ (i {n} sono le parti che cambiano: numeri, nomi…)
  function compile(src, dst) {
    const parts = src.split(/\{(\d)\}/);
    let re = '^';
    const order = [];
    // una parte che cambia è un valore (numero, nome, elenco), mai più frasi
    parts.forEach((p, i) => { if (i % 2) { re += '((?:(?![.!?] )[\\s\\S])+?)'; order.push(+p); } else re += esc(p); });
    return { re: new RegExp(re + '$'), order, dst, fixed: src.replace(/\{\d\}/g, '').length };
  }

  function load(code) {
    const d = (window.PCB_LANG || {})[code];
    if (!d) return false;
    exact.clear();
    Object.entries(d.exact || {}).forEach(([k, v]) => exact.set(k, v));
    // prima i modelli più lunghi (più specifici)
    patterns = (d.patterns || []).map(([a, b]) => compile(a, b)).sort((x, y) => y.fixed - x.fixed);
    return true;
  }

  function core(s, depth) {
    if (exact.has(s)) return exact.get(s);
    for (const p of patterns) {
      const m = p.re.exec(s);
      if (!m) continue;
      return p.dst.replace(/\{(\d)\}/g, (_, n) => {
        const v = m[p.order.indexOf(+n) + 1];
        if (v === undefined) return '';
        let t = depth < 2 ? core(v, depth + 1) : null;
        if (t == null && depth < 2 && v.includes(', ')) {        // elenchi: "x, y, z"
          let changed = false;
          const parts = v.split(', ').map(x => { const y = core(x, depth + 1); if (y != null) { changed = true; return y; } return x; });
          if (changed) t = parts.join(', ');
        }
        return t == null ? v : t;
      });
    }
    return null;
  }

  // Traduce un testo. Se non c'è intero nel dizionario, prova frase per frase.
  function tr(text) {
    if (lang === 'it' || text == null) return text;
    const s = String(text);
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s);
    const body = m[2];
    if (!body || !/[A-Za-zÀ-ÿ]/.test(body)) return s;
    let t = core(body, 0);
    if (t == null) {
      const pieces = body.split(/(?<=[.!?…:])\s+/);
      if (pieces.length > 1) {
        let changed = false;
        const out = pieces.map(p => { const x = core(p, 0); if (x != null) { changed = true; return x; } return p; });
        if (changed) t = out.join(' ');
      }
    }
    if (t == null) { if (window.PCB_I18N_DEBUG) missing.add(body); return s; }
    return m[1] + t + m[3];
  }
  const missing = new Set();

  const ATTRS = ['title', 'placeholder', 'aria-label'];
  function node(n) {
    if (n.nodeType === 3) {
      const p = n.parentNode;
      if (!p || p.nodeName === 'SCRIPT' || p.nodeName === 'STYLE') return;
      const v = n.nodeValue;
      if (done.get(n) === v) return;
      const t = tr(v);
      done.set(n, t);
      if (t !== v) n.nodeValue = t;
    } else if (n.nodeType === 1) {
      ATTRS.forEach(a => {
        const v = n.getAttribute(a);
        if (v) { const t = tr(v); if (t !== v) n.setAttribute(a, t); }
      });
    }
  }
  function tree(root) {
    if (lang === 'it' || !root) return;
    if (root.nodeType === 3) return node(root);
    node(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    while (w.nextNode()) node(w.currentNode);
  }
  let observer = null;
  function watch() {
    if (lang === 'it' || observer) return;
    observer = new MutationObserver(muts => {
      for (const m of muts) {
        if (m.type === 'characterData') node(m.target);
        else if (m.type === 'attributes') node(m.target);
        else m.addedNodes.forEach(tree);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  // "auto" = la lingua di Windows (italiano se è italiano, altrimenti inglese)
  function resolve(pref, sys) {
    if (pref === 'it' || pref === 'en') return pref;
    if (sys === 'it' || sys === 'en') return sys;          // lingua di Windows, letta dal programma
    const l = (navigator.languages && navigator.languages[0]) || navigator.language || 'it';
    return l.toLowerCase().startsWith('it') ? 'it' : 'en';
  }
  function setLang(pref, sys) {
    const l = resolve(pref, sys);
    lang = l !== 'it' && load(l) ? l : 'it';
    document.documentElement.lang = lang;
    return lang;
  }
  function start(pref, sys) {
    setLang(pref, sys);
    if (lang !== 'it') { tree(document.body); watch(); document.title = tr(document.title); }
    document.documentElement.classList.remove('i18n-wait');
    return lang;
  }
  return {
    tr, start, setLang, resolve, missing,
    get lang() { return lang; },
    locale: () => (lang === 'en' ? 'en-GB' : 'it-IT'),
  };
})();
