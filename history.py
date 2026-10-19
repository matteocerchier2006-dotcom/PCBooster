"""
PC Booster - cronologia delle modifiche, con "Annulla".

Ogni volta che l'app cambia qualcosa sul PC lo scrive qui (%APPDATA%\\PCBooster\\cronologia.json).
Le modifiche che si possono annullare hanno un campo "undo" con quello che serve per tornare indietro.
"""
import os
import json
import time
import threading

import updater
import winutil

_lock = threading.RLock()
_last_id = 0.0
MAX = 300


def _path():
    return os.path.join(os.path.dirname(updater._settings_path()), "cronologia.json")


def load():
    data = winutil.read_json(_path(), [])
    return [e for e in data if isinstance(e, dict) and "id" in e] if isinstance(data, list) else []


def _save(items):
    try:
        winutil.atomic_write(_path(), json.dumps(items[-MAX:], ensure_ascii=False, indent=1))
    except OSError:
        pass   # la cronologia non deve mai bloccare un'operazione


def add(kind, label, detail="", undo=None):
    """kind: optimize, startup, clean, disk, apps, security, network, game, driver, update…"""
    global _last_id
    with _lock:
        now = max(time.time(), _last_id + 0.000001)
        _last_id = now
    entry = {"id": f"{now:.6f}", "when": time.strftime("%Y-%m-%d %H:%M"), "kind": kind,
             "label": label, "detail": detail, "undo": undo, "undone": False}
    with _lock:
        items = load()
        items.append(entry)
        _save(items)
    return entry


def get(entry_id):
    return next((e for e in load() if e["id"] == entry_id), None)


def mark_undone(entry_id):
    with _lock:
        items = load()
        for e in items:
            if e["id"] == entry_id:
                e["undone"] = True
        _save(items)


def clear():
    with _lock:
        _save([])
