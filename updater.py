"""
PC Booster - aggiornamento automatico dell'app.

Come funziona:
1. Tu pubblichi una nuova release su GitHub: GitHub crea PCBooster.exe e lo allega.
2. All'avvio, l'app di ogni PC chiede a GitHub qual è l'ultima versione.
3. Se è più nuova, la scarica in silenzio e la installa quando chiudi l'app
   (oppure subito, se premi "Aggiorna ora" nelle Impostazioni).
"""
import os
import sys
import json
import time
import hashlib
import threading
import subprocess
import urllib.request

import winutil

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
IS_FROZEN = getattr(sys, "frozen", False)  # True quando gira come .exe


def _base_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _read_bundled(name: str) -> str:
    try:
        with open(os.path.join(_base_dir(), name), encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return ""


# Repository GitHub "nomeutente/PCBooster": lo scrive GitHub in repo.txt quando crea l'exe
GITHUB_REPO = _read_bundled("repo.txt")


def app_version() -> str:
    return _read_bundled("version.txt") or "0.0.0"


def _parse(v: str):
    parts = []
    for p in v.strip().lstrip("vV").split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


# ---------------- impostazioni (salvate in %APPDATA%\PCBooster) ----------------
DEFAULT_SETTINGS = {
    "auto_update": True,
    # aspetto
    "theme": "dark", "accent": "#8b7bff", "font": "barlow", "font_size": "normal", "radius": "medium",
    "compact_sidebar": False, "animations": True, "glow": True, "start_page": "home",
    # sicurezza
    "restore_before_optimize": True,
    # avvio senza richiesta dei permessi (attività di Windows + collegamento sul desktop)
    "no_uac_prompt": True,
    "last_version": "",
    # icona vicino all'orologio
    "tray_enabled": True, "start_with_windows": False, "notifications": True,
    "tray_tip_seen": False, "notified": {},
    # modalità partita (gestite dall'app, non dalle preferenze dell'interfaccia)
    "game_mode_state": None,
    "game_mode_opts": {"power": True, "ram": True, "close": True, "reopen": True},
    "game_mode_close": None,
}
def _settings_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "PCBooster")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "settings.json")


_settings_lock = threading.RLock()


def _read_settings_file():
    path = _settings_path()
    for candidate in (path, path + ".bak"):
        data = winutil.read_json(candidate)
        if isinstance(data, dict):
            return data
    return {}


def load_settings():
    defaults = dict(DEFAULT_SETTINGS)
    with _settings_lock:
        defaults.update(_read_settings_file())
    return defaults


def save_settings(data):
    """Salva le impostazioni in modo sicuro (anche se due parti dell'app salvano insieme
    o il PC si spegne a metà, il file non si rovina). Tiene anche una copia .bak."""
    with _settings_lock:
        s = load_settings()
        s.update(data or {})
        path = _settings_path()
        text = json.dumps(s, indent=2, ensure_ascii=False)
        try:
            winutil.atomic_write(path, text)
            winutil.atomic_write(path + ".bak", text)
        except OSError as e:
            log(f"Impostazioni non salvate: {e}")
    return s


# ---------------- controllo e installazione ----------------
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "PCBooster-Updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def check_for_update():
    current = app_version()
    if not GITHUB_REPO:
        return {"ok": False, "available": False, "current": current,
                "msg": "Aggiornamenti non configurati: crea una release su GitHub."}
    try:
        rel = _get_json(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
    except Exception as e:
        return {"ok": False, "available": False, "current": current,
                "msg": f"Non riesco a contattare GitHub ({e.__class__.__name__}). Controlla la connessione."}
    if not isinstance(rel, dict):
        return {"ok": False, "available": False, "current": current, "msg": "Risposta di GitHub non valida."}
    latest = str(rel.get("tag_name") or "0.0.0").lstrip("vV")
    asset = next((a for a in rel.get("assets") or [] if str(a.get("name", "")).lower().endswith(".exe")), None)
    digest = str((asset or {}).get("digest") or "")
    available = _parse(latest) > _parse(current) and asset is not None
    return {
        "ok": True, "available": available, "current": current, "latest": latest,
        "notes": (rel.get("body") or "").strip()[:600],
        "url": asset.get("browser_download_url") if asset else None,
        "size": asset.get("size") if asset else 0,
        "sha256": digest[7:].lower() if digest.startswith("sha256:") else "",
        "msg": f"È disponibile la versione {latest}." if available else f"Hai già l'ultima versione ({current}).",
    }


def _update_dir():
    """Dove scaricare la versione nuova: accanto all'app (cartella protetta di Program Files,
    stesso disco: la sostituzione è istantanea); se non si può scrivere lì, nei dati dell'app."""
    here = os.path.dirname(sys.executable)
    try:
        test = os.path.join(here, ".pcb_write_test")
        with open(test, "w") as f:
            f.write("1")
        os.remove(test)
        return here
    except OSError:
        folder = os.path.join(os.path.dirname(_settings_path()), "update")
        os.makedirs(folder, exist_ok=True)
        return folder


def download_update(url: str, expected_size: int = 0, sha256: str = ""):
    """Scarica il nuovo .exe e lo controlla. Non tocca ancora l'app installata."""
    if not IS_FROZEN:
        return {"ok": False, "msg": "Stai usando la versione da sorgente: gli aggiornamenti arrivano solo all'exe."}
    if not url or not url.lower().startswith(f"https://github.com/{GITHUB_REPO}/releases/download/".lower()):
        return {"ok": False, "msg": "Indirizzo di download non valido."}
    folder = _update_dir()
    new_exe = os.path.join(folder, "PCBooster.exe.new")
    part = new_exe + ".part"
    h = hashlib.sha256()
    size = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PCBooster-Updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            final = r.geturl().lower()
            if not final.startswith("https://"):
                raise IOError(f"indirizzo finale non sicuro: {final}")
            while chunk := r.read(1024 * 256):
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
    except Exception as e:
        log(f"Download dell'aggiornamento non riuscito: {e}")
        _silent_remove(part)
        return {"ok": False, "msg": f"Download non riuscito ({e.__class__.__name__}). Riprova più tardi."}
    problem = ""
    if size < 1_000_000 or (expected_size and size != expected_size):
        problem = f"file incompleto ({size} byte invece di {expected_size})"
    elif sha256 and h.hexdigest() != sha256:
        problem = "il controllo SHA-256 non corrisponde a quello di GitHub"
    else:
        with open(part, "rb") as f:
            if f.read(2) != b"MZ":
                problem = "non è un programma Windows"
    if problem:
        log(f"Aggiornamento scartato: {problem}")
        _silent_remove(part)
        return {"ok": False, "msg": "Il file scaricato non è valido: per sicurezza non l'ho installato. Riprova più tardi."}
    try:
        os.replace(part, new_exe)
    except OSError as e:
        log(f"Non riesco a preparare l'aggiornamento: {e}")
        _silent_remove(part)
        return {"ok": False, "msg": "Non riesco a preparare l'aggiornamento. Riprova più tardi."}
    log(f"Aggiornamento scaricato e controllato ({size} byte" + (", SHA-256 ok)" if sha256 else ")"))
    return {"ok": True, "path": new_exe, "msg": "Aggiornamento scaricato."}


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def log(msg):
    """Diario degli aggiornamenti: %APPDATA%\\PCBooster\\aggiornamenti.log (utile se qualcosa va storto)."""
    try:
        import time
        path = os.path.join(os.path.dirname(_settings_path()), "aggiornamenti.log")
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def cleanup_old_versions():
    """All'avvio: elimina le copie vecchie lasciate dall'ultimo aggiornamento."""
    if not IS_FROZEN:
        return
    import glob
    import shutil
    import tempfile
    for old in glob.glob(sys.executable + "*.old"):
        try:
            os.remove(old)
            log(f"Eliminata la versione vecchia {old}")
        except OSError:
            pass
    # download lasciati a metà o mai installati
    for folder in {os.path.dirname(sys.executable), os.path.join(os.path.dirname(_settings_path()), "update")}:
        for leftover in glob.glob(os.path.join(folder, "PCBooster.exe.new*")):
            _silent_remove(leftover)
    # cartelle temporanee delle versioni precedenti (fino alla 1.5.0)
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "pcbooster_*")):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


def relaunch():
    """Riapre l'app dopo 3 secondi (così la finestra vecchia ha il tempo di chiudersi)."""
    import launcher
    target = sys.executable
    sys32 = os.path.dirname(winutil.tool("schtasks"))
    ping = os.path.join(sys32, "PING.EXE")
    if launcher.task_exists():
        run = f'"{winutil.tool("schtasks")}" /Run /TN "{launcher.TASK}"'
    else:
        run = f'start "" "{target}"'
    cmd = f'"{ping}" -n 4 127.0.0.1 >nul & {run}'
    for flags in (CREATE_NO_WINDOW | DETACHED_PROCESS | 0x01000000, CREATE_NO_WINDOW | DETACHED_PROCESS):
        try:   # 0x01000000 = esci dal "gruppo" dell'app, così Windows non lo chiude insieme a lei
            subprocess.Popen([winutil.tool("cmd"), "/c", cmd], creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, cwd=sys32)
            log("Riavvio programmato")
            return True
        except OSError as e:
            log(f"Riavvio non riuscito con flag {flags}: {e}")
    return False


def apply_update(new_exe: str, restart: bool = True):
    """Mette subito la versione nuova al posto di quella in uso.

    Windows non permette di cancellare un programma aperto, ma permette di rinominarlo:
    1) PCBooster.exe (in uso)  ->  PCBooster.exe.<numero>.old
    2) versione nuova          ->  PCBooster.exe
    La copia .old viene eliminata al prossimo avvio.
    """
    if not IS_FROZEN or not new_exe or not os.path.isfile(new_exe):
        return {"ok": False, "msg": "Nessun aggiornamento pronto."}
    import shutil
    target = sys.executable
    old = f"{target}.{int(time.time())}.old"
    log(f"Aggiornamento: {new_exe} -> {target}")
    try:
        os.replace(target, old)
    except OSError as e:
        log(f"Non riesco a rinominare l'app in uso: {e}")
        return {"ok": False, "msg": "Non riesco a sostituire l'app: riprova oppure scarica la versione nuova da GitHub."}
    try:
        try:
            os.replace(new_exe, target)        # stesso disco: istantaneo
        except OSError:
            shutil.move(new_exe, target)       # dischi diversi: copia
    except Exception as e:
        log(f"Non riesco a spostare la versione nuova: {e}")
        try:
            os.replace(old, target)   # rimette tutto com'era
        except OSError as e2:
            log(f"ATTENZIONE, ripristino non riuscito: {e2}")
        return {"ok": False, "msg": "Non riesco a sostituire l'app: riprova oppure scarica la versione nuova da GitHub."}
    log("Versione nuova installata")
    if restart:
        relaunch()
        return {"ok": True, "msg": "Aggiornato: l'app si riapre tra qualche secondo."}
    return {"ok": True, "msg": "Aggiornato: la prossima volta che apri PC Booster avrai la versione nuova."}


def install_update(url: str, expected_size: int = 0, sha256: str = ""):
    """Scarica e installa subito (l'app va chiusa dopo questa chiamata)."""
    r = download_update(url, expected_size, sha256)
    return apply_update(r["path"], True) if r["ok"] else r
