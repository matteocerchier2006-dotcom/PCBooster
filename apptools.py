"""
PC Booster - aggiornamento delle app (winget) e Modalità partita.

winget è lo strumento ufficiale di Windows per installare e aggiornare i programmi
(si chiama "Programma di installazione app" nel Microsoft Store).
"""
import os
import re
import sys
import time
import ctypes
import shutil
import subprocess

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

try:
    import psutil
except ImportError:
    psutil = None


# ======================================================================
# Aggiornamento app con winget
# ======================================================================
def _winget():
    exe = shutil.which("winget")
    if exe:
        return exe
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\WindowsApps\winget.exe")
    return p if os.path.exists(p) else None


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("oem" if IS_WINDOWS else "latin-1", errors="replace")


def parse_winget_table(text: str):
    """Legge la tabella di 'winget upgrade' (funziona in italiano e in inglese)."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.split("\r")[-1]          # toglie l'animazione di caricamento
        lines.append(line.rstrip())
    sep = next((i for i, l in enumerate(lines) if re.fullmatch(r"-{10,}", l.strip())), None)
    if sep is None or sep == 0:
        return []
    header = lines[sep - 1]
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    if len(starts) < 4:
        return []
    has_source = len(starts) >= 5
    rows = []
    for line in lines[sep + 1:]:
        if not line.strip() or re.match(r"^\d+\s", line.strip()):
            break
        # Si legge da destra: [Origine] Disponibile Versione ID, il resto è il nome (che può avere spazi)
        t = line.split()
        if len(t) < (5 if has_source else 4):
            continue
        source = t.pop() if has_source else ""
        new, cur = t.pop(), t.pop()
        if t and t[-1] in ("<", ">"):
            cur = t.pop() + " " + cur
        pid = t.pop() if t else ""
        name = " ".join(t).rstrip("…").strip()
        if not pid or not name or "unknown" in (cur.lower(), new.lower()) or "sconosciuto" in (cur.lower(), new.lower()):
            continue
        rows.append({"name": name, "id": pid, "version": cur, "available": new, "source": source})
    return rows


DEMO_UPGRADES = [
    {"name": "Discord", "id": "Discord.Discord", "version": "1.0.9205", "available": "1.0.9212", "source": "winget"},
    {"name": "Google Chrome", "id": "Google.Chrome.EXE", "version": "140.0.7339.208", "available": "141.0.7390.66", "source": "winget"},
    {"name": "7-Zip 24.08 (x64)", "id": "7zip.7zip", "version": "24.08", "available": "25.01", "source": "winget"},
    {"name": "Steam", "id": "Valve.Steam", "version": "2.10.91.91", "available": "3.0", "source": "winget"},
    {"name": "VLC media player", "id": "VideoLAN.VLC", "version": "3.0.20", "available": "3.0.21", "source": "winget"},
]


def list_app_updates():
    if not IS_WINDOWS:
        return {"ok": True, "apps": [dict(a) for a in DEMO_UPGRADES]}
    exe = _winget()
    if not exe:
        return {"ok": False, "missing": True,
                "msg": "Manca \"Programma di installazione app\" di Microsoft: installalo dal Microsoft Store."}
    try:
        r = subprocess.run([exe, "upgrade", "--accept-source-agreements", "--disable-interactivity"],
                           capture_output=True, timeout=180, creationflags=CREATE_NO_WINDOW,
                           env={**os.environ, "NO_COLOR": "1"})
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "winget non ha risposto in tempo. Riprova."}
    apps = [a for a in parse_winget_table(_decode(r.stdout)) if "pcbooster" not in a["id"].lower()]
    return {"ok": True, "apps": apps}


def upgrade_app(pkg_id: str):
    if not re.fullmatch(r"[\w.\-+]+", pkg_id or ""):
        return {"ok": False, "msg": "Programma non valido."}
    if not IS_WINDOWS:
        time.sleep(1.2)
        return {"ok": pkg_id != "Valve.Steam", "msg": "Aggiornato." if pkg_id != "Valve.Steam" else
                "Non riuscito: chiudi il programma e riprova."}
    exe = _winget()
    if not exe:
        return {"ok": False, "msg": "winget non trovato."}
    try:
        r = subprocess.run([exe, "upgrade", "--id", pkg_id, "--exact", "--silent", "--accept-package-agreements",
                            "--accept-source-agreements", "--disable-interactivity"],
                           capture_output=True, timeout=1800, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "Ci sta mettendo troppo: riprova più tardi."}
    if r.returncode == 0:
        return {"ok": True, "msg": "Aggiornato."}
    out = _decode(r.stdout).lower()
    if "in use" in out or "in uso" in out or "running" in out:
        return {"ok": False, "msg": "Non riuscito: chiudi il programma e riprova."}
    return {"ok": False, "msg": "Non riuscito: prova ad aggiornarlo dal programma stesso."}


def open_winget_store():
    if IS_WINDOWS:
        os.startfile("ms-windows-store://pdp/?productid=9NBLGGH4NNS1")
    return True


# ======================================================================
# Modalità partita
# ======================================================================
# Processi che non vanno mai chiusi (sistema, driver, launcher dei giochi, PC Booster)
NEVER = {
    "system", "idle", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "explorer.exe", "sihost.exe", "taskhostw.exe",
    "ctfmon.exe", "runtimebroker.exe", "searchhost.exe", "searchindexer.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "msedgewebview2.exe", "applicationframehost.exe",
    "securityhealthsystray.exe", "securityhealthservice.exe", "msmpeng.exe", "nissrv.exe", "audiodg.exe",
    "conhost.exe", "dllhost.exe", "spoolsv.exe", "wmiprvse.exe", "memcompression", "lockapp.exe",
    "pcbooster.exe", "pc booster.exe", "python.exe", "pythonw.exe", "powershell.exe", "cmd.exe",
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "epicwebhelper.exe", "battle.net.exe",
    "agent.exe", "riotclientservices.exe", "riotclientux.exe", "eadesktop.exe", "ea.exe", "upc.exe",
    "ubisoftconnect.exe", "galaxyclient.exe", "xboxpcapp.exe", "gamebar.exe", "gamebarftserver.exe",
    "taskmgr.exe", "useroobebroker.exe", "smartscreen.exe", "crossdeviceresume.exe",
}
NEVER_PREFIX = ("nvidia", "nv", "amd", "radeon", "rtk", "realtek", "intel", "igfx", "asus", "msi", "razer",
                "corsair", "icue", "logi", "lghub", "steelseries", "vgc", "vgtray", "easyanticheat", "beservice",
                "faceit", "vanguard")
# App che di solito conviene chiudere durante una partita (selezionate in automatico)
SUGGEST = ("chrome.exe", "msedge.exe", "firefox.exe", "zen.exe", "brave.exe", "opera.exe", "vivaldi.exe",
           "onedrive.exe", "teams.exe", "ms-teams.exe", "skype.exe", "zoom.exe", "slack.exe", "dropbox.exe",
           "googledrivefs.exe", "acrobat.exe", "acrord32.exe", "creative cloud.exe", "adobe desktop service.exe",
           "widgets.exe", "phoneexperiencehost.exe", "yourphone.exe", "whatsapp.exe", "telegram.exe",
           "utorrent.exe", "qbittorrent.exe", "copilot.exe")
LABELS = {"chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox", "zen.exe": "Zen Browser",
          "brave.exe": "Brave", "opera.exe": "Opera", "vivaldi.exe": "Vivaldi", "onedrive.exe": "OneDrive",
          "teams.exe": "Microsoft Teams", "ms-teams.exe": "Microsoft Teams", "discord.exe": "Discord",
          "spotify.exe": "Spotify", "widgets.exe": "Widget di Windows", "phoneexperiencehost.exe": "Collegamento al telefono",
          "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram", "copilot.exe": "Copilot"}

DEMO_RUNNING = [("chrome.exe", 1850), ("discord.exe", 640), ("spotify.exe", 410), ("onedrive.exe", 120),
                ("widgets.exe", 180), ("msedge.exe", 520), ("whatsapp.exe", 260)]


def running_apps():
    """App aperte (raggruppate per nome) che si possono chiudere, con la RAM che usano."""
    if not IS_WINDOWS or psutil is None:
        return [{"exe": n, "label": LABELS.get(n, n[:-4].capitalize()), "mem_mb": m, "suggest": n in SUGGEST}
                for n, m in DEMO_RUNNING]
    windir = os.environ.get("WINDIR", r"C:\Windows").lower()
    groups = {}
    me = os.getpid()
    for p in psutil.process_iter(["name", "exe", "memory_info", "username", "pid"]):
        try:
            name = (p.info["name"] or "").lower()
            if p.info["pid"] in (0, 4, me) or not name or name in NEVER or name.startswith(NEVER_PREFIX):
                continue
            path = p.info["exe"] or ""
            if not path or path.lower().startswith(windir) or not p.info["username"]:
                continue
            g = groups.setdefault(name, {"exe": name, "path": path, "mem": 0})
            g["mem"] += p.info["memory_info"].rss if p.info["memory_info"] else 0
        except (psutil.Error, OSError):
            continue
    apps = []
    for name, g in groups.items():
        mb = round(g["mem"] / 1024 ** 2)
        if mb < 60:
            continue
        label = LABELS.get(name) or os.path.splitext(name)[0].replace("_", " ").title()
        apps.append({"exe": name, "label": label, "mem_mb": mb, "suggest": name in SUGGEST})
    apps.sort(key=lambda a: a["mem_mb"], reverse=True)
    return apps[:25]


def _close(names):
    """Chiude con gentilezza (come la X della finestra); se dopo 3 secondi sono ancora aperte, le forza."""
    closed = []
    if not IS_WINDOWS or psutil is None:
        return [{"exe": n, "path": ""} for n in names]
    paths = {}
    for p in psutil.process_iter(["name", "exe"]):
        try:
            n = (p.info["name"] or "").lower()
            if n in names and p.info["exe"]:
                paths.setdefault(n, p.info["exe"])
        except psutil.Error:
            pass
    for n in names:
        subprocess.run(["taskkill", "/IM", n], capture_output=True, creationflags=CREATE_NO_WINDOW)
    time.sleep(3)
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() in names:
                p.kill()
        except psutil.Error:
            pass
    for n in names:
        closed.append({"exe": n, "path": paths.get(n, "")})
    return closed


def trim_memory():
    """Chiede a Windows di liberare la RAM inutilizzata di tutti i programmi."""
    if not IS_WINDOWS or psutil is None:
        return 1.4 * 1024 ** 3
    before = psutil.virtual_memory().available
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    for p in psutil.process_iter():
        try:
            h = k32.OpenProcess(0x0100 | 0x0400, False, p.pid)   # SET_QUOTA | QUERY_INFORMATION
            if h:
                psapi.EmptyWorkingSet(h)
                k32.CloseHandle(h)
        except Exception:
            pass
    time.sleep(1)
    return max(0, psutil.virtual_memory().available - before)


HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def _active_plan():
    if not IS_WINDOWS:
        return "381b4222-f694-41f0-9685-ff5bb260df2e"
    out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, errors="replace",
                         creationflags=CREATE_NO_WINDOW).stdout
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out, re.I)
    return m.group(0) if m else ""


def _set_plan(guid):
    if not IS_WINDOWS or not guid:
        return True
    r = subprocess.run(["powercfg", "/setactive", guid], capture_output=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0 and guid == HIGH_PERF:
        subprocess.run(["powercfg", "-duplicatescheme", guid], capture_output=True, creationflags=CREATE_NO_WINDOW)
        r = subprocess.run(["powercfg", "/setactive", guid], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def human(b):
    return f"{b / 1024 ** 3:.1f} GB" if b >= 1024 ** 3 else f"{b / 1024 ** 2:.0f} MB"


def start_game_mode(opts, close_names):
    """opts: {'power': bool, 'ram': bool, 'close': bool}. Restituisce lo stato da salvare."""
    state = {"active": True, "since": time.strftime("%H:%M"), "prev_plan": "", "closed": [], "ram_freed": 0}
    parts = []
    if opts.get("power", True):
        state["prev_plan"] = _active_plan()
        if state["prev_plan"].lower() != HIGH_PERF and _set_plan(HIGH_PERF):
            parts.append("prestazioni elevate attive")
    if opts.get("close", True) and close_names:
        names = [n.lower() for n in close_names if n.lower() not in NEVER]
        state["closed"] = _close(names)
        if state["closed"]:
            parts.append(f"{len(state['closed'])} app chiuse")
    if opts.get("ram", True):
        freed = trim_memory()
        state["ram_freed"] = freed
        parts.append(f"{human(freed)} di RAM liberata")
    state["msg"] = "Modalità partita attiva: " + (", ".join(parts) if parts else "pronta") + "."
    return state


def stop_game_mode(state, reopen=True):
    parts = []
    if state.get("prev_plan") and state["prev_plan"].lower() != HIGH_PERF:
        if _set_plan(state["prev_plan"]):
            parts.append("piano energetico ripristinato")
    if reopen and IS_WINDOWS:
        n = 0
        for c in state.get("closed", []):
            if c.get("path") and os.path.exists(c["path"]):
                try:
                    subprocess.Popen([c["path"]], cwd=os.path.dirname(c["path"]))
                    n += 1
                except OSError:
                    pass
        if n:
            parts.append(f"{n} app riaperte")
    elif reopen and state.get("closed"):
        parts.append(f"{len(state['closed'])} app riaperte")
    return {"ok": True, "msg": "Modalità partita terminata" + (": " + ", ".join(parts) if parts else "") + "."}
