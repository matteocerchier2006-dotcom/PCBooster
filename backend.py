"""
PC Booster - motore dell'applicazione.
Tutte le operazioni reali passano da PowerShell/registro di Windows.
Su un sistema non-Windows restituisce dati di esempio (utile solo per provare l'interfaccia).
"""
import os
import sys
import json
import glob
import shutil
import ctypes
import datetime
import subprocess
import threading

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg

try:
    import psutil
except ImportError:  # psutil è facoltativo
    psutil = None


# --------------------------------------------------------------------------
# Utilità
# --------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000


def run_ps(script: str, timeout: int = 300) -> str:
    """Esegue uno script PowerShell senza aprire finestre e restituisce l'output."""
    if not IS_WINDOWS:
        return ""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )
    return completed.stdout.strip()


def run_ps_json(script: str, timeout: int = 300):
    out = run_ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout)
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def is_admin() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Riavvia il programma chiedendo i permessi di amministratore (finestra UAC)."""
    if not IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):  # .exe creato con PyInstaller
        exe, params = sys.executable, " ".join(sys.argv[1:])
    else:
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def folder_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def parse_wmi_date(value) -> str:
    """Converte le date WMI/CIM (es. '20240315000000.000000-000' o '/Date(…)/') in AAAA-MM-GG."""
    if not value:
        return ""
    s = str(value)
    if s.startswith("/Date("):
        try:
            ms = int(s[6:].split(")")[0].split("+")[0].split("-")[0])
            return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def days_since(date_str: str):
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return (datetime.datetime.now() - d).days
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Siti ufficiali dei driver
# --------------------------------------------------------------------------
DRIVER_PAGES = {
    "nvidia": "https://www.nvidia.com/it-it/drivers/",
    "amd": "https://www.amd.com/it/support/download/drivers.html",
    "intel": "https://www.intel.it/content/www/it/it/support/detect.html",
}
CPU_PAGES = {
    "intel": "https://www.intel.it/content/www/it/it/support/detect.html",
    "amd": "https://www.amd.com/it/support/download/drivers.html",
}


def vendor_of(name: str) -> str:
    n = (name or "").lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "gtx" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n or "ryzen" in n:
        return "amd"
    if "intel" in n or "arc" in n:
        return "intel"
    return "altro"


# --------------------------------------------------------------------------
# Dati di esempio (solo fuori da Windows, per vedere l'interfaccia)
# --------------------------------------------------------------------------
DEMO = {
    "system": {"os": "Windows 11 Pro 24H2", "cpu": "AMD Ryzen 7 5800X 8-Core", "cores": 8, "threads": 16,
               "ram_gb": 32, "gpu": "NVIDIA GeForce RTX 3070", "pc_name": "PC-DEMO",
               "cpu_load": 14, "ram_used": 41, "disk_free_gb": 212, "disk_total_gb": 931, "admin": True},
    "gpus": [{"name": "NVIDIA GeForce RTX 3070", "vendor": "nvidia", "driver_version": "566.36",
              "driver_date": "2025-12-05", "age_days": 313, "vram_gb": 8, "status": "old",
              "download_page": DRIVER_PAGES["nvidia"]}],
}


# --------------------------------------------------------------------------
# API esposta all'interfaccia
# --------------------------------------------------------------------------
class Api:
    def __init__(self):
        self._apps_cache = []
        self._lock = threading.Lock()

    # ---------------- Panoramica ----------------
    def get_system_info(self):
        if not IS_WINDOWS:
            return DEMO["system"]
        info = run_ps_json(
            "$os=Get-CimInstance Win32_OperatingSystem;"
            "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;"
            "$gpu=(Get-CimInstance Win32_VideoController | Where-Object {$_.Name -notmatch 'Basic|Remote|Virtual'} "
            "| Select-Object -First 1).Name;"
            "[pscustomobject]@{os=$os.Caption; build=$os.BuildNumber; cpu=$cpu.Name.Trim();"
            "cores=$cpu.NumberOfCores; threads=$cpu.NumberOfLogicalProcessors;"
            "ram=[math]::Round($os.TotalVisibleMemorySize/1MB); gpu=$gpu; pc=$env:COMPUTERNAME}"
        )
        i = info[0] if info else {}
        result = {
            "os": f"{i.get('os', 'Windows')} (build {i.get('build', '?')})",
            "cpu": i.get("cpu", "?"), "cores": i.get("cores", 0), "threads": i.get("threads", 0),
            "ram_gb": i.get("ram", 0), "gpu": i.get("gpu") or "Non rilevata", "pc_name": i.get("pc", ""),
            "admin": is_admin(),
        }
        result.update(self.get_live_stats())
        return result

    def get_live_stats(self):
        if psutil is None:
            return {"cpu_load": 0, "ram_used": 0, "disk_free_gb": 0, "disk_total_gb": 0}
        drive = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"
        disk = psutil.disk_usage(drive)
        return {
            "cpu_load": round(psutil.cpu_percent(interval=0.4)),
            "ram_used": round(psutil.virtual_memory().percent),
            "disk_free_gb": round(disk.free / 1024 ** 3),
            "disk_total_gb": round(disk.total / 1024 ** 3),
        }

    # ---------------- Scheda video ----------------
    def get_gpu_info(self):
        if not IS_WINDOWS:
            return DEMO["gpus"]
        rows = run_ps_json(
            "Get-CimInstance Win32_VideoController | Where-Object {$_.Name -notmatch 'Basic Display|Remote|Virtual|Mirage'} "
            "| Select-Object Name, DriverVersion, DriverDate, AdapterRAM"
        )
        gpus = []
        for r in rows:
            name = r.get("Name", "")
            vendor = vendor_of(name)
            version = r.get("DriverVersion", "")
            # NVIDIA: la versione "vera" è più leggibile da nvidia-smi (es. 566.36)
            if vendor == "nvidia":
                smi = shutil.which("nvidia-smi")
                if smi:
                    try:
                        v = subprocess.run([smi, "--query-gpu=driver_version", "--format=csv,noheader"],
                                           capture_output=True, text=True, timeout=10,
                                           creationflags=CREATE_NO_WINDOW).stdout.strip().splitlines()
                        if v:
                            version = v[0]
                    except Exception:
                        pass
            date = parse_wmi_date(r.get("DriverDate"))
            age = days_since(date)
            status = "ok" if age is not None and age < 120 else ("old" if age is not None else "unknown")
            vram = r.get("AdapterRAM") or 0
            vram_gb = round(vram / 1024 ** 3) if vram and vram > 0 else None  # WMI limita a 4 GB
            gpus.append({
                "name": name, "vendor": vendor, "driver_version": version, "driver_date": date,
                "age_days": age, "vram_gb": vram_gb, "status": status,
                "download_page": DRIVER_PAGES.get(vendor, ""),
            })
        return gpus

    def get_gpu_tweaks(self):
        """Stato attuale delle ottimizzazioni grafiche."""
        if not IS_WINDOWS:
            return {"game_mode": True, "hags": False, "power_plan": "Bilanciato", "high_perf": False,
                    "game_dvr": True}
        return {
            "game_mode": self._reg_get(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar",
                                       "AutoGameModeEnabled", 1) == 1,
            "hags": self._reg_get(winreg.HKEY_LOCAL_MACHINE,
                                  r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 1) == 2,
            "game_dvr": self._reg_get(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore",
                                      "GameDVR_Enabled", 1) == 1,
            **self._power_plan(),
        }

    def set_gpu_tweak(self, name, enabled):
        if not IS_WINDOWS:
            return {"ok": True, "msg": "Modalità demo: nessuna modifica fatta."}
        try:
            if name == "game_mode":
                self._reg_set(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar",
                              "AutoGameModeEnabled", 1 if enabled else 0)
                return {"ok": True, "msg": "Modalità Gioco " + ("attivata." if enabled else "disattivata.")}
            if name == "hags":
                if not is_admin():
                    return {"ok": False, "msg": "Servono i permessi di amministratore."}
                self._reg_set(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                              "HwSchMode", 2 if enabled else 1)
                return {"ok": True, "msg": "Fatto. Riavvia il PC per applicare la pianificazione GPU."}
            if name == "game_dvr":
                # La registrazione in background della Xbox Game Bar consuma risorse durante il gioco
                self._reg_set(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore",
                              "GameDVR_Enabled", 1 if enabled else 0)
                self._reg_set(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                              "AppCaptureEnabled", 1 if enabled else 0)
                return {"ok": True, "msg": "Registrazione in background " + ("attivata." if enabled else "disattivata.")}
            if name == "high_perf":
                guid = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c" if enabled else "381b4222-f694-41f0-9685-ff5bb260df2e"
                r = subprocess.run(["powercfg", "/setactive", guid], capture_output=True, text=True,
                                   creationflags=CREATE_NO_WINDOW)
                if r.returncode != 0 and enabled:
                    # Su alcuni PC il piano "Prestazioni elevate" è nascosto: lo ricreiamo
                    subprocess.run(["powercfg", "-duplicatescheme", guid], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW)
                    r = subprocess.run(["powercfg", "/setactive", guid], capture_output=True, text=True,
                                       creationflags=CREATE_NO_WINDOW)
                if r.returncode != 0:
                    return {"ok": False, "msg": "Impossibile cambiare il piano energetico."}
                return {"ok": True, "msg": "Piano energetico: " + ("Prestazioni elevate." if enabled else "Bilanciato.")}
        except PermissionError:
            return {"ok": False, "msg": "Permesso negato: avvia l'app come amministratore."}
        except Exception as e:
            return {"ok": False, "msg": f"Errore: {e}"}
        return {"ok": False, "msg": "Opzione sconosciuta."}

    def _power_plan(self):
        out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True,
                             errors="replace", creationflags=CREATE_NO_WINDOW).stdout
        name = out.split("(")[-1].rstrip(") \r\n") if "(" in out else "Sconosciuto"
        return {"power_plan": name, "high_perf": "8c5e7fda" in out or "ultimate" in out.lower()
                or "e9a42b02" in out}

    @staticmethod
    def _reg_get(root, path, name, default=None):
        try:
            with winreg.OpenKey(root, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return default

    @staticmethod
    def _reg_set(root, path, name, value):
        with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))

    def open_url(self, url):
        if url.startswith("https://"):
            if IS_WINDOWS:
                os.startfile(url)
            return True
        return False

    def open_vendor_app(self, vendor):
        """Apre l'app ufficiale del produttore (NVIDIA App / AMD Adrenalin), se installata."""
        if not IS_WINDOWS:
            return {"ok": False, "msg": "Modalità demo."}
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates = {
            "nvidia": [rf"{pf}\NVIDIA Corporation\NVIDIA App\CEF\NVIDIA App.exe",
                       rf"{pf}\NVIDIA Corporation\NVIDIA GeForce Experience\NVIDIA GeForce Experience.exe"],
            "amd": [rf"{pf}\AMD\CNext\CNext\RadeonSoftware.exe"],
            "intel": [rf"{pf}\Intel\Intel Graphics Software\IntelGraphicsSoftware.exe"],
        }.get(vendor, [])
        for path in candidates:
            if os.path.exists(path):
                os.startfile(path)
                return {"ok": True, "msg": "App del produttore aperta: da lì puoi installare l'ultimo driver."}
        return {"ok": False, "msg": "App del produttore non trovata: apro il sito ufficiale."}

    # ---------------- Aggiornamenti (Windows Update: driver GPU, chipset/CPU, sistema) ----------------
    def check_updates(self):
        if not IS_WINDOWS:
            return {"ok": True, "updates": [
                {"title": "NVIDIA - Display - 32.0.15.7680", "type": "Driver", "category": "gpu", "size": "812.4 MB"},
                {"title": "AMD - System - 2.13.27.501 (chipset)", "type": "Driver", "category": "cpu", "size": "1.2 MB"},
                {"title": "2026-10 Aggiornamento cumulativo per Windows 11", "type": "Software", "category": "system",
                 "size": "640.0 MB"}]}
        script = r"""
$s = New-Object -ComObject Microsoft.Update.Session
$searcher = $s.CreateUpdateSearcher()
$searcher.Online = $true
$r = $searcher.Search("IsInstalled=0 and IsHidden=0")
$list = @()
foreach ($u in $r.Updates) {
  $t = if ($u.Type -eq 2) {'Driver'} else {'Software'}
  $cls = ''
  try { $cls = $u.DriverClass } catch {}
  $list += [pscustomobject]@{ title=$u.Title; type=$t; driverClass=$cls; size=[double]$u.MaxDownloadSize; id=$u.Identity.UpdateID }
}
$list
"""
        try:
            rows = run_ps_json(script, timeout=600)
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "Windows Update non ha risposto in tempo. Riprova."}
        updates = []
        for r in rows:
            title = r.get("title", "")
            cls = (r.get("driverClass") or "").lower()
            t = title.lower()
            if cls == "display" or "display" in t or vendor_of(t) == "nvidia":
                cat = "gpu"
            elif cls in ("system", "processor") or "chipset" in t or "processor" in t or "microcode" in t:
                cat = "cpu"
            else:
                cat = "system"
            updates.append({"title": title, "type": r.get("type"), "category": cat,
                            "size": human_size(r.get("size") or 0), "id": r.get("id")})
        self._pending_updates = updates
        return {"ok": True, "updates": updates}

    def install_updates(self, ids):
        """Scarica e installa gli aggiornamenti scelti tramite Windows Update."""
        if not IS_WINDOWS:
            return {"ok": True, "msg": "Modalità demo: niente installato.", "reboot": False}
        if not is_admin():
            return {"ok": False, "msg": "Servono i permessi di amministratore."}
        id_list = ",".join(f"'{i}'" for i in ids if all(c.isalnum() or c == '-' for c in str(i)))
        if not id_list:
            return {"ok": False, "msg": "Nessun aggiornamento selezionato."}
        script = rf"""
$want = @({id_list})
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
$col = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) {{ if ($want -contains $u.Identity.UpdateID) {{ if (-not $u.EulaAccepted) {{ $u.AcceptEula() }}; [void]$col.Add($u) }} }}
if ($col.Count -eq 0) {{ [pscustomobject]@{{n=0; code=0; reboot=$false}}; return }}
$d = $s.CreateUpdateDownloader(); $d.Updates = $col; [void]$d.Download()
$i = $s.CreateUpdateInstaller(); $i.Updates = $col; $res = $i.Install()
[pscustomobject]@{{n=$col.Count; code=$res.ResultCode; reboot=$res.RebootRequired}}
"""
        try:
            rows = run_ps_json(script, timeout=3600)
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "L'installazione sta impiegando troppo. Controlla Windows Update."}
        r = rows[0] if rows else {}
        ok = r.get("code") in (2, 3)  # 2 = riuscito, 3 = riuscito con errori
        msg = f"Installati {r.get('n', 0)} aggiornamenti." if ok else "Installazione non riuscita."
        if r.get("reboot"):
            msg += " Riavvia il PC per completare."
        return {"ok": ok, "msg": msg, "reboot": bool(r.get("reboot"))}

    def get_cpu_info(self):
        if not IS_WINDOWS:
            return {"name": "AMD Ryzen 7 5800X 8-Core", "vendor": "amd", "max_mhz": 3801, "cores": 8,
                    "threads": 16, "bios": "2025-03-11", "chipset_page": CPU_PAGES["amd"]}
        rows = run_ps_json(
            "$c=Get-CimInstance Win32_Processor | Select-Object -First 1; $b=Get-CimInstance Win32_BIOS;"
            "[pscustomobject]@{name=$c.Name.Trim(); mhz=$c.MaxClockSpeed; cores=$c.NumberOfCores;"
            "threads=$c.NumberOfLogicalProcessors; bios=$b.ReleaseDate; biosver=$b.SMBIOSBIOSVersion}"
        )
        r = rows[0] if rows else {}
        vendor = vendor_of(r.get("name", ""))
        return {"name": r.get("name", "?"), "vendor": vendor, "max_mhz": r.get("mhz"), "cores": r.get("cores"),
                "threads": r.get("threads"), "bios": parse_wmi_date(r.get("bios")),
                "bios_version": r.get("biosver", ""), "chipset_page": CPU_PAGES.get(vendor, "")}

    # ---------------- Ottimizza tutto ----------------
    OPTIMAL = [("game_mode", True), ("hags", True), ("high_perf", True), ("game_dvr", False)]

    def optimize_all(self):
        """Applica in un colpo solo tutte le ottimizzazioni per il gioco."""
        results = [self.set_gpu_tweak(name, value) for name, value in self.OPTIMAL]
        done = sum(1 for r in results if r["ok"])
        failed = [r["msg"] for r in results if not r["ok"]]
        msg = f"{done} ottimizzazioni su {len(results)} applicate."
        if failed:
            msg += " Non riuscite: " + " ".join(failed)
        else:
            msg += " Riavvia il PC per attivarle tutte."
        return {"ok": done > 0, "msg": msg, "done": done, "total": len(results)}

    # ---------------- Avvio automatico ----------------
    # Programmi che di solito conviene NON far partire con Windows
    STARTUP_SUGGEST_OFF = ("discord", "steam", "epic", "spotify", "teams", "skype", "battle.net", "ubisoft",
                           "uplay", "ea desktop", "eadesktop", "origin", "gog galaxy", "riot", "opera", "edge",
                           "msedge", "ccleaner", "adobe", "acrobat", "creative cloud", "itunes", "zoom",
                           "whatsapp", "telegram", "dropbox", "google drive", "utorrent", "qbittorrent",
                           "cortana", "xbox", "rockstar", "wallpaper engine", "icue", "razer", "logitech", "lghub")
    # Programmi da lasciare attivi (sicurezza, audio, driver)
    STARTUP_KEEP = ("securityhealth", "windows defender", "realtek", "nvidia", "amd", "radeon", "intel",
                    "synaptics", "elan", "onedrive", "audio", "bluetooth", "touchpad")

    _APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

    def _startup_sources(self):
        env = os.environ
        hkcu, hklm = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
        run = r"Software\Microsoft\Windows\CurrentVersion\Run"
        return [
            # (tipo, radice, chiave/cartella, chiave StartupApproved, radice approved, etichetta)
            ("reg", hkcu, run, "Run", hkcu, "Solo il tuo utente"),
            ("reg", hklm, run, "Run", hklm, "Tutti gli utenti"),
            ("reg", hklm, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Run32", hklm,
             "Tutti gli utenti"),
            ("dir", None, os.path.join(env.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
             "StartupFolder", hkcu, "Cartella Esecuzione automatica"),
            ("dir", None, os.path.join(env.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\StartUp"),
             "StartupFolder", hklm, "Cartella Esecuzione automatica"),
        ]

    def _suggestion(self, name, command):
        text = f"{name} {command}".lower()
        if any(k in text for k in self.STARTUP_KEEP):
            return "keep"
        if any(k in text for k in self.STARTUP_SUGGEST_OFF):
            return "off"
        return ""

    def list_startup(self):
        if not IS_WINDOWS:
            demo = [("Discord", r"C:\Users\Demo\AppData\Local\Discord\Update.exe --processStart Discord.exe", True),
                    ("Steam", r'"C:\Program Files (x86)\Steam\steam.exe" -silent', True),
                    ("EpicGamesLauncher", r"C:\Program Files (x86)\Epic Games\Launcher\EpicGamesLauncher.exe -silent", True),
                    ("Spotify", r"C:\Users\Demo\AppData\Roaming\Spotify\Spotify.exe /minimized", False),
                    ("SecurityHealth", r"%windir%\system32\SecurityHealthSystray.exe", True),
                    ("RtkAudUService", r"C:\Windows\System32\RtkAudUService64.exe -background", True),
                    ("OneDrive", r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background', True)]
            self._startup_cache = [{"id": str(i), "name": n, "command": c, "enabled": e, "where": "Solo il tuo utente",
                                    "suggest": self._suggestion(n, c)} for i, (n, c, e) in enumerate(demo)]
            return self._startup_cache
        items = []
        for kind, root, path, approved, aroot, where in self._startup_sources():
            entries = []
            if kind == "reg":
                try:
                    with winreg.OpenKey(root, path) as k:
                        for i in range(winreg.QueryInfoKey(k)[1]):
                            name, cmd, _t = winreg.EnumValue(k, i)
                            if name:
                                entries.append((name, str(cmd)))
                except OSError:
                    continue
            else:
                if not os.path.isdir(path):
                    continue
                entries = [(f, os.path.join(path, f)) for f in os.listdir(path)
                           if f.lower() != "desktop.ini"]
            for name, cmd in entries:
                enabled = True
                try:
                    with winreg.OpenKey(aroot, rf"{self._APPROVED}\{approved}") as ak:
                        data = winreg.QueryValueEx(ak, name)[0]
                        if isinstance(data, bytes) and data and data[0] & 1:
                            enabled = False  # byte dispari = disattivato (come in Gestione attività)
                except OSError:
                    pass
                label = os.path.splitext(name)[0] if kind == "dir" else name
                items.append({"id": str(len(items)), "name": label, "command": cmd, "enabled": enabled,
                              "where": where, "suggest": self._suggestion(label, cmd),
                              "_value": name, "_approved": approved, "_aroot": aroot})
        items.sort(key=lambda x: (x["suggest"] != "off", x["name"].lower()))
        self._startup_cache = items
        return [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]

    def set_startup(self, item_id, enabled):
        """Attiva/disattiva un programma all'avvio, nello stesso modo di Gestione attività (reversibile)."""
        item = next((x for x in getattr(self, "_startup_cache", []) if x["id"] == item_id), None)
        if not item:
            return {"ok": False, "msg": "Elemento non trovato, aggiorna l'elenco."}
        if not IS_WINDOWS:
            item["enabled"] = enabled
            return {"ok": True, "msg": f"{item['name']} " + ("riattivato." if enabled else "non partirà più all'avvio.")}
        if item["_aroot"] == winreg.HKEY_LOCAL_MACHINE and not is_admin():
            return {"ok": False, "msg": "Per questo programma servono i permessi di amministratore."}
        if enabled:
            data = b"\x02" + b"\x00" * 11
        else:
            filetime = int((datetime.datetime.utcnow() - datetime.datetime(1601, 1, 1)).total_seconds() * 10 ** 7)
            data = b"\x03\x00\x00\x00" + filetime.to_bytes(8, "little")
        try:
            with winreg.CreateKeyEx(item["_aroot"], rf"{self._APPROVED}\{item['_approved']}", 0,
                                    winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, item["_value"], 0, winreg.REG_BINARY, data)
        except PermissionError:
            return {"ok": False, "msg": "Permesso negato: avvia l'app come amministratore."}
        item["enabled"] = enabled
        return {"ok": True, "msg": f"{item['name']} " + ("riattivato." if enabled else "non partirà più all'avvio.")}

    def disable_suggested_startup(self):
        if not getattr(self, "_startup_cache", None):
            self.list_startup()
        targets = [x for x in self._startup_cache if x["enabled"] and x["suggest"] == "off"]
        ok = [x["name"] for x in targets if self.set_startup(x["id"], False)["ok"]]
        if not targets:
            return {"ok": True, "msg": "Niente da disattivare: l'avvio è già pulito."}
        return {"ok": bool(ok), "msg": f"Disattivati all'avvio: {', '.join(ok)}." if ok else "Nessun programma disattivato."}

    # ---------------- Pulizia ----------------
    def _clean_targets(self):
        if not IS_WINDOWS:
            return []
        env = os.environ
        local = env.get("LOCALAPPDATA", "")
        windir = env.get("WINDIR", r"C:\Windows")
        return [
            ("user_temp", "File temporanei dell'utente", [env.get("TEMP", "")]),
            ("win_temp", "File temporanei di Windows", [os.path.join(windir, "Temp")]),
            ("shader", "Cache shader DirectX / GPU", [os.path.join(local, "D3DSCache"),
                                                     os.path.join(local, "NVIDIA", "DXCache"),
                                                     os.path.join(local, "NVIDIA", "GLCache"),
                                                     os.path.join(local, "AMD", "DxCache"),
                                                     os.path.join(local, "AMD", "DxcCache")]),
            ("thumbs", "Cache miniature", [os.path.join(local, "Microsoft", "Windows", "Explorer")]),
            ("wu_cache", "Download vecchi di Windows Update",
             [os.path.join(windir, "SoftwareDistribution", "Download")]),
            ("crash", "Report errori e dump", [os.path.join(local, "CrashDumps"),
                                               os.path.join(env.get("PROGRAMDATA", ""), "Microsoft", "Windows", "WER")]),
        ]

    def scan_junk(self):
        if not IS_WINDOWS:
            return [{"id": "user_temp", "label": "File temporanei dell'utente", "bytes": 2_400_000_000, "size": "2.2 GB"},
                    {"id": "win_temp", "label": "File temporanei di Windows", "bytes": 310_000_000, "size": "295.6 MB"},
                    {"id": "shader", "label": "Cache shader DirectX / GPU", "bytes": 1_100_000_000, "size": "1.0 GB"},
                    {"id": "thumbs", "label": "Cache miniature", "bytes": 180_000_000, "size": "171.7 MB"},
                    {"id": "wu_cache", "label": "Download vecchi di Windows Update", "bytes": 3_900_000_000, "size": "3.6 GB"},
                    {"id": "crash", "label": "Report errori e dump", "bytes": 95_000_000, "size": "90.6 MB"},
                    {"id": "recycle", "label": "Cestino", "bytes": 640_000_000, "size": "610.4 MB"}]
        results = []
        for tid, label, paths in self._clean_targets():
            total = 0
            for p in paths:
                if not p or not os.path.isdir(p):
                    continue
                if tid == "thumbs":
                    total += sum(os.path.getsize(f) for f in glob.glob(os.path.join(p, "thumbcache_*.db"))
                                 if os.path.isfile(f))
                else:
                    total += folder_size(p)
            results.append({"id": tid, "label": label, "bytes": total, "size": human_size(total)})
        # Cestino
        rb = run_ps("$s=0; (New-Object -ComObject Shell.Application).NameSpace(10).Items() | "
                    "ForEach-Object { $s += $_.Size }; $s")
        try:
            rb_bytes = int(float(rb or 0))
        except ValueError:
            rb_bytes = 0
        results.append({"id": "recycle", "label": "Cestino", "bytes": rb_bytes, "size": human_size(rb_bytes)})
        return results

    def clean_junk(self, ids):
        if not IS_WINDOWS:
            return {"ok": True, "freed": "Demo", "msg": "Modalità demo: nessun file eliminato."}
        freed = 0
        skipped = 0
        targets = {tid: paths for tid, _l, paths in self._clean_targets()}
        for tid in ids:
            if tid == "recycle":
                before = sum(r["bytes"] for r in self.scan_junk() if r["id"] == "recycle")
                run_ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
                freed += before
                continue
            if tid == "wu_cache":
                run_ps("Stop-Service wuauserv -Force -ErrorAction SilentlyContinue")
            for p in targets.get(tid, []):
                if not p or not os.path.isdir(p):
                    continue
                if tid == "thumbs":
                    entries = glob.glob(os.path.join(p, "thumbcache_*.db"))
                else:
                    entries = [os.path.join(p, e) for e in os.listdir(p)]
                for e in entries:
                    try:
                        size = os.path.getsize(e) if os.path.isfile(e) else folder_size(e)
                        if os.path.isdir(e) and not os.path.islink(e):
                            shutil.rmtree(e)
                        else:
                            os.remove(e)
                        freed += size
                    except OSError:
                        skipped += 1  # file in uso: viene saltato, è normale
            if tid == "wu_cache":
                run_ps("Start-Service wuauserv -ErrorAction SilentlyContinue")
        msg = f"Liberati {human_size(freed)}."
        if skipped:
            msg += f" {skipped} elementi erano in uso e sono stati lasciati."
        return {"ok": True, "freed": human_size(freed), "msg": msg}

    # ---------------- App e giochi ----------------
    GAME_PUBLISHERS = ("valve", "epic games", "electronic arts", "ubisoft", "rockstar", "activision", "blizzard",
                       "riot games", "bethesda", "cd projekt", "2k", "bandai", "capcom", "square enix", "sega",
                       "paradox", "mojang", "hoyoverse", "cognosphere", "gog", "battle.net", "focus entertainment",
                       "devolver", "frontier", "creative assembly", "warner bros", "konami", "nexon")

    def list_apps(self):
        if not IS_WINDOWS:
            apps = [
                {"id": "a1", "name": "Cyberpunk 2077", "publisher": "CD PROJEKT RED", "size_mb": 71200, "date": "2025-02-10", "game": True},
                {"id": "a2", "name": "Counter-Strike 2", "publisher": "Valve", "size_mb": 33400, "date": "2026-05-01", "game": True},
                {"id": "a3", "name": "Fortnite", "publisher": "Epic Games", "size_mb": 28100, "date": "2024-11-20", "game": True},
                {"id": "a4", "name": "Google Chrome", "publisher": "Google LLC", "size_mb": 420, "date": "2026-10-01", "game": False},
                {"id": "a5", "name": "Discord", "publisher": "Discord Inc.", "size_mb": 380, "date": "2026-09-12", "game": False},
                {"id": "a6", "name": "WinRAR 7.01", "publisher": "win.rar GmbH", "size_mb": 9, "date": "2024-06-03", "game": False},
                {"id": "a7", "name": "McAfee WebAdvisor", "publisher": "McAfee, LLC", "size_mb": 64, "date": "2023-01-15", "game": False},
            ]
            self._apps_cache = apps
            return apps
        apps, seen = [], set()
        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for root, path in roots:
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, sub_name) as sub:
                        def val(n, d=None):
                            try:
                                return winreg.QueryValueEx(sub, n)[0]
                            except OSError:
                                return d
                        name = val("DisplayName")
                        uninstall = val("QuietUninstallString") or val("UninstallString")
                        if not name or not uninstall or val("SystemComponent") == 1 or val("ParentKeyName"):
                            continue
                        if name in seen:
                            continue
                        seen.add(name)
                        publisher = val("Publisher", "") or ""
                        size_kb = val("EstimatedSize", 0) or 0
                        location = (val("InstallLocation", "") or "").lower()
                        d = str(val("InstallDate", "") or "")
                        is_game = (sub_name.startswith("Steam App ") or "steamapps" in location
                                   or "epic games" in location or "\\games\\" in location
                                   or any(p in publisher.lower() for p in self.GAME_PUBLISHERS))
                        apps.append({
                            "id": f"{len(apps)}", "name": name, "publisher": publisher,
                            "size_mb": round(size_kb / 1024) if isinstance(size_kb, int) else 0,
                            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else "",
                            "game": bool(is_game), "_uninstall": uninstall,
                        })
                except OSError:
                    continue
            winreg.CloseKey(key)
        apps.sort(key=lambda a: a["size_mb"], reverse=True)
        with self._lock:
            self._apps_cache = apps
        return [{k: v for k, v in a.items() if not k.startswith("_")} for a in apps]

    def uninstall_app(self, app_id):
        """Avvia il programma di disinstallazione ufficiale dell'app scelta."""
        app = next((a for a in self._apps_cache if a["id"] == app_id), None)
        if not app:
            return {"ok": False, "msg": "App non trovata, aggiorna l'elenco."}
        if not IS_WINDOWS:
            return {"ok": True, "msg": f"Demo: avrei avviato la disinstallazione di {app['name']}."}
        cmd = app["_uninstall"]
        try:
            # MsiExec con /I apre la riparazione: usiamo /X per disinstallare
            if "msiexec" in cmd.lower():
                cmd = cmd.replace("/I", "/X").replace("/i", "/X")
            subprocess.Popen(cmd, shell=True)
            return {"ok": True, "msg": f"Disinstallazione di {app['name']} avviata: segui la finestra che si è aperta."}
        except Exception as e:
            return {"ok": False, "msg": f"Impossibile avviare la disinstallazione: {e}"}

    def open_windows_settings(self, page):
        allowed = {"apps": "ms-settings:appsfeatures", "update": "ms-settings:windowsupdate",
                   "storage": "ms-settings:storagesense", "graphics": "ms-settings:display-advancedgraphics"}
        if IS_WINDOWS and page in allowed:
            os.startfile(allowed[page])
        return True

    def restart_as_admin(self):
        return relaunch_as_admin()
