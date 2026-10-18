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
import tempfile
import subprocess
import urllib.request

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


def load_settings():
    defaults = dict(DEFAULT_SETTINGS)
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            defaults.update(json.load(f))
    except (OSError, ValueError):
        pass
    return defaults


def save_settings(data):
    s = load_settings()
    s.update(data)
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
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
    latest = rel.get("tag_name", "0.0.0").lstrip("vV")
    asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
    available = _parse(latest) > _parse(current) and asset is not None
    return {
        "ok": True, "available": available, "current": current, "latest": latest,
        "notes": (rel.get("body") or "").strip()[:600],
        "url": asset.get("browser_download_url") if asset else None,
        "size": asset.get("size") if asset else 0,
        "msg": f"È disponibile la versione {latest}." if available else f"Hai già l'ultima versione ({current}).",
    }


def download_update(url: str, expected_size: int = 0):
    """Scarica il nuovo .exe in una cartella temporanea. Non tocca ancora l'app installata."""
    if not IS_FROZEN:
        return {"ok": False, "msg": "Stai usando la versione da sorgente: gli aggiornamenti arrivano solo all'exe."}
    if not url or not url.lower().startswith(f"https://github.com/{GITHUB_REPO}/releases/download/".lower()):
        return {"ok": False, "msg": "Indirizzo di download non valido."}
    tmp_dir = tempfile.mkdtemp(prefix="pcbooster_")
    new_exe = os.path.join(tmp_dir, "PC Booster.new.exe")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PCBooster-Updater"})
        with urllib.request.urlopen(req, timeout=120) as r, open(new_exe, "wb") as f:
            while chunk := r.read(1024 * 256):
                f.write(chunk)
    except Exception as e:
        return {"ok": False, "msg": f"Download non riuscito ({e.__class__.__name__}). Riprova più tardi."}
    size = os.path.getsize(new_exe)
    if size < 1_000_000 or (expected_size and size != expected_size):
        return {"ok": False, "msg": "Il file scaricato è incompleto. Riprova più tardi."}
    return {"ok": True, "path": new_exe, "msg": "Aggiornamento scaricato."}


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
    for old in glob.glob(sys.executable + "*.old"):
        try:
            os.remove(old)
            log(f"Eliminata la versione vecchia {old}")
        except OSError:
            pass


def relaunch():
    """Riapre l'app dopo 3 secondi (così la finestra vecchia ha il tempo di chiudersi)."""
    import launcher
    target = sys.executable
    if launcher.task_exists():
        run = f'schtasks /Run /TN "{launcher.TASK}"'
    else:
        run = f'start "" "{target}"'
    cmd = f"ping -n 4 127.0.0.1 >nul & {run}"
    for flags in (CREATE_NO_WINDOW | DETACHED_PROCESS | 0x01000000, CREATE_NO_WINDOW | DETACHED_PROCESS):
        try:   # 0x01000000 = esci dal "gruppo" dell'app, così Windows non lo chiude insieme a lei
            subprocess.Popen(["cmd", "/c", cmd], creationflags=flags, close_fds=True)
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
    import time
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
        shutil.move(new_exe, target)
    except Exception as e:
        log(f"Non riesco a spostare la versione nuova: {e}")
        try:
            os.replace(old, target)   # rimette tutto com'era
        except OSError as e2:
            log(f"ATTENZIONE, ripristino non riuscito: {e2}")
        return {"ok": False, "msg": "Non riesco a sostituire l'app: riprova oppure scarica la versione nuova da GitHub."}
    shutil.rmtree(os.path.dirname(new_exe), ignore_errors=True)
    log("Versione nuova installata")
    if restart:
        relaunch()
        return {"ok": True, "msg": "Aggiornato: l'app si riapre tra qualche secondo."}
    return {"ok": True, "msg": "Aggiornato: la prossima volta che apri PC Booster avrai la versione nuova."}


def install_update(url: str, expected_size: int = 0):
    """Scarica e installa subito (l'app va chiusa dopo questa chiamata)."""
    r = download_update(url, expected_size)
    return apply_update(r["path"], True) if r["ok"] else r
