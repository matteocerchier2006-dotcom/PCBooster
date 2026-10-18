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


def apply_update(new_exe: str, restart: bool = True):
    """Sostituisce l'exe appena l'app si chiude. Con restart=True la riapre subito dopo."""
    if not IS_FROZEN or not new_exe or not os.path.isfile(new_exe):
        return {"ok": False, "msg": "Nessun aggiornamento pronto."}
    target = sys.executable
    tmp_dir = os.path.dirname(new_exe)
    bat = os.path.join(tmp_dir, "aggiorna.bat")
    pid = os.getpid()
    reopen = f'start "" "{target}"' if restart else "rem"
    with open(bat, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as f:
        f.write(f"""@echo off
:aspetta
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto aspetta)
set /a tentativi=0
:sostituisci
move /y "{new_exe}" "{target}" >nul 2>&1
if errorlevel 1 (
  set /a tentativi+=1
  if %tentativi% lss 20 (timeout /t 1 /nobreak >nul & goto sostituisci)
)
{reopen}
(goto) 2>nul & rmdir /s /q "{tmp_dir}"
""")
    subprocess.Popen(["cmd", "/c", bat], creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)
    return {"ok": True, "msg": "L'app si riapre aggiornata tra qualche secondo." if restart
            else "L'aggiornamento verrà installato alla chiusura."}


def install_update(url: str, expected_size: int = 0):
    """Scarica e installa subito (l'app va chiusa dopo questa chiamata)."""
    r = download_update(url, expected_size)
    return apply_update(r["path"], True) if r["ok"] else r
