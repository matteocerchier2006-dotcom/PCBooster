"""
PC Booster - lingue per la parte in Python (menu dell'icona vicino all'orologio e notifiche).

Usa lo stesso dizionario dell'interfaccia (ui/lang-en.js), così una traduzione si scrive una volta sola.
Lingua: impostazione "lang" (auto = come Windows: italiano se Windows è in italiano, altrimenti inglese).
"""
import os
import re
import sys
import json

import updater

_cache = {}


def _base_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _load(code):
    if code in _cache:
        return _cache[code]
    data = {"exact": {}, "patterns": []}
    try:
        with open(os.path.join(_base_dir(), "ui", f"lang-{code}.js"), encoding="utf-8") as f:
            text = f.read()
        start = text.index("=", text.index(f"PCB_LANG.{code}")) + 1
        data = json.loads(text[start:].strip().rstrip(";"))
    except (OSError, ValueError):
        pass
    pats = []
    for src, dst in data.get("patterns", []):
        parts = re.split(r"\{(\d)\}", src)
        rx, order = "^", []
        for i, p in enumerate(parts):
            if i % 2:
                rx += r"((?:(?![.!?] )[\s\S])+?)"     # un valore, mai più frasi
                order.append(int(p))
            else:
                rx += re.escape(p)
        pats.append((re.compile(rx + "$"), order, dst, len(re.sub(r"\{\d\}", "", src))))
    pats.sort(key=lambda x: -x[3])
    _cache[code] = (data.get("exact", {}), pats)
    return _cache[code]


def windows_is_italian():
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x10   # 0x10 = italiano
    except Exception:
        return True


def current():
    pref = updater.load_settings().get("lang", "auto")
    if pref in ("it", "en"):
        return pref
    return "it" if windows_is_italian() else "en"


def _core(s, exact, pats, depth=0):
    if s in exact:
        return exact[s]
    for rx, order, dst, _n in pats:
        m = rx.match(s)
        if not m:
            continue

        def fill(mm):
            idx = order.index(int(mm.group(1))) if int(mm.group(1)) in order else -1
            v = m.group(idx + 1) if idx >= 0 else ""
            t = _core(v, exact, pats, depth + 1) if depth < 2 else None
            if t is None and depth < 2 and ", " in v:
                parts = [(_core(x, exact, pats, depth + 1) or x) for x in v.split(", ")]
                t = ", ".join(parts)
            return v if t is None else t
        return re.sub(r"\{(\d)\}", fill, dst)
    return None


def tr(text):
    """Traduce un testo nella lingua scelta (se manca nel dizionario resta in italiano)."""
    lang = current()
    if lang == "it" or not text:
        return text
    exact, pats = _load(lang)
    s = str(text).strip()
    t = _core(s, exact, pats)
    if t is None:
        pieces = re.split(r"(?<=[.!?…:])\s+", s)
        if len(pieces) > 1:
            out = [(_core(p, exact, pats) or p) for p in pieces]
            if out != pieces:
                t = " ".join(out)
    return text if t is None else t
