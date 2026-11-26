// PC Booster - grafici dell'utilizzo (disegnati qui, senza librerie esterne).
// Regole: linee sottili (2px), griglia leggera, un solo asse; una serie usa il colore principale,
// due serie usano due colori controllati anche per chi distingue male i colori (--series-1/2).
// Il valore sotto il mouse compare nel riquadro, e gli stessi numeri sono sempre scritti sotto il grafico.
const Charts = (() => {
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function setup(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return null;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  // colore con trasparenza (per la leggera "ombra" sotto la linea)
  function alpha(color, a) {
    const c = color.trim();
    if (/^#([0-9a-f]{6})$/i.test(c)) {
      const n = parseInt(c.slice(1), 16);
      return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
    }
    return c;
  }

  // scala "tonda" per l'asse: 0 → 1, 2, 5, 10, 20, 50…
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
    return 10 * p;
  }

  function path(ctx, pts) {
    let started = false;
    ctx.beginPath();
    pts.forEach(p => {
      if (p == null) { started = false; return; }          // buco nei dati: la linea si interrompe
      if (!started) { ctx.moveTo(p[0], p[1]); started = true; } else ctx.lineTo(p[0], p[1]);
    });
  }

  // ---------------------------------------------------------------- mini grafico (riquadri Panoramica)
  function spark(canvas, values, max = 100) {
    const g = setup(canvas); if (!g) return;
    const { ctx, w, h } = g;
    const vals = values.filter(v => v != null);
    if (vals.length < 2) return;
    const top = max || niceMax(Math.max(...vals));
    const color = css('--accent');
    const step = w / (values.length - 1);
    const pts = values.map((v, i) => v == null ? null : [i * step, h - 2 - (Math.min(v, top) / top) * (h - 4)]);
    // area leggera (~12%) e linea da 2px
    const real = pts.filter(Boolean);
    ctx.beginPath(); ctx.moveTo(real[0][0], h); real.forEach(p => ctx.lineTo(p[0], p[1])); ctx.lineTo(real[real.length - 1][0], h); ctx.closePath();
    ctx.fillStyle = alpha(color, 0.12); ctx.fill();
    path(ctx, pts); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();
    const last = real[real.length - 1];
    ctx.beginPath(); ctx.arc(last[0] - 1, last[1], 3, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
  }

  // ---------------------------------------------------------------- grafico completo con assi
  // o = { t:[secondi], now, range, series:[{name, values, color}], max (fisso, es. 100) o null (automatico), fmt(v) }
  function line(canvas, o) {
    const g = setup(canvas); if (!g) return;
    const { ctx, w, h } = g;
    const all = o.series.flatMap(s => s.values.filter(v => v != null));
    const top = o.max || niceMax(Math.max(1e-9, ...all) * 1.1);
    const font = css('--body') || 'sans-serif';
    ctx.font = `11px ${font}`;
    // margine sinistro largo quanto serve alle etichette (così non vengono mai tagliate)
    const ticks = [0, 0.5, 1].map(f => o.fmt(top * f, true));
    const pad = { l: Math.ceil(Math.max(...ticks.map(t => ctx.measureText(t).width))) + 14, r: 10, t: 10, b: 24 };
    const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    const x = t => pad.l + (1 - (o.now - t) / o.range) * pw;
    const y = v => pad.t + ph - (Math.min(v, top) / top) * ph;
    ctx.fillStyle = css('--muted'); ctx.strokeStyle = css('--line'); ctx.lineWidth = 1;
    // griglia: 3 linee piene e sottili
    [0, 0.5, 1].forEach((f, i) => {
      const yy = Math.round(pad.t + ph - f * ph) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(ticks[i], pad.l - 8, yy);
    });
    // tempo: inizio, metà, adesso
    ctx.textBaseline = 'top';
    const label = s => s === 0 ? I18N.tr('ora') : (s >= 120 ? `-${+(s / 60).toFixed(1)} min` : `-${s} s`);
    [[o.range, 'left'], [o.range / 2, 'center'], [0, 'right']].forEach(([s, al]) => {
      ctx.textAlign = al;
      ctx.fillText(label(Math.round(s)), al === 'left' ? pad.l : al === 'right' ? w - pad.r : pad.l + pw / 2, h - pad.b + 7);
    });
    // linee: con un buco dove mancano i dati (es. app chiusa)
    const gap = (o.interval || 2) * 2.5;
    o.series.forEach(s => {
      const pts = [];
      s.values.forEach((v, i) => {
        if (v == null) { pts.push(null); return; }
        if (i > 0 && o.t[i] - o.t[i - 1] > gap) pts.push(null);
        pts.push([x(o.t[i]), y(v)]);
      });
      if (o.series.length === 1) {
        const real = pts.filter(Boolean);
        if (real.length > 1) {
          ctx.beginPath(); ctx.moveTo(real[0][0], pad.t + ph); real.forEach(p => ctx.lineTo(p[0], p[1]));
          ctx.lineTo(real[real.length - 1][0], pad.t + ph); ctx.closePath();
          ctx.fillStyle = alpha(s.color, 0.10); ctx.fill();
        }
      }
      path(ctx, pts); ctx.strokeStyle = s.color; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();
    });
    canvas._geo = { o, x, y, pad, pw, ph, w, h };
  }

  // crosshair + riquadro con i valori (tutte le serie insieme), anche da tastiera
  function hover(canvas, tip) {
    if (canvas._hover) return;
    canvas._hover = true;
    const show = clientX => {
      const g = canvas._geo; if (!g || !g.o.t.length) return;
      const r = canvas.getBoundingClientRect();
      const px = Math.max(g.pad.l, Math.min(clientX - r.left, g.pad.l + g.pw));
      let best = 0, bd = Infinity;
      g.o.t.forEach((t, i) => { const d = Math.abs(g.x(t) - px); if (d < bd) { bd = d; best = i; } });
      line(canvas, g.o);                                   // ridisegna, poi la linea verticale
      const ctx = canvas.getContext('2d'), xx = Math.round(g.x(g.o.t[best])) + 0.5;
      ctx.strokeStyle = css('--muted'); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(xx, g.pad.t); ctx.lineTo(xx, g.pad.t + g.ph); ctx.stroke();
      tip.textContent = '';
      const ago = Math.round(g.o.now - g.o.t[best]);
      const head = document.createElement('small');
      head.textContent = ago < 3 ? I18N.tr('adesso') : I18N.tr('{0} fa').replace('{0}', ago >= 120 ? `${Math.round(ago / 60)} min` : `${ago} s`);
      tip.append(head);
      g.o.series.forEach(s => {
        const v = s.values[best];
        if (v != null) {
          ctx.beginPath(); ctx.arc(g.x(g.o.t[best]), g.y(v), 4, 0, Math.PI * 2);
          ctx.fillStyle = s.color; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = css('--surface'); ctx.stroke();
        }
        const row = document.createElement('div');
        const key = document.createElement('i'); key.style.background = s.color;
        const val = document.createElement('b'); val.textContent = v == null ? '—' : g.o.fmt(v);
        const name = document.createElement('span'); name.textContent = s.name;
        row.append(key, val, name); tip.append(row);
      });
      tip.hidden = false;
      const tx = g.x(g.o.t[best]);
      tip.style.left = Math.min(Math.max(tx - tip.offsetWidth / 2, 0), g.w - tip.offsetWidth) + 'px';
    };
    canvas.addEventListener('pointermove', e => show(e.clientX));
    canvas.addEventListener('pointerleave', () => { tip.hidden = true; if (canvas._geo) line(canvas, canvas._geo.o); });
    canvas.tabIndex = 0;
    canvas.addEventListener('focus', () => { const r = canvas.getBoundingClientRect(); show(r.right - 12); });
    canvas.addEventListener('blur', () => { tip.hidden = true; if (canvas._geo) line(canvas, canvas._geo.o); });
  }

  return { spark, line, hover, niceMax };
})();
