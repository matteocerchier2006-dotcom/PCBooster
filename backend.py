"""
PC Booster - motore dell'applicazione.
Tutte le operazioni reali passano da PowerShell, registro di Windows e strumenti di sistema.
Su un sistema non-Windows restituisce dati di esempio (utile solo per provare l'interfaccia).
"""
import os
import sys
import json
import shutil
import ctypes
import datetime
import subprocess
import threading

from cleaner import Cleaner, human_size
import drivers
import apptools

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg

try:
    import psutil
except ImportError:
    psutil = None

CREATE_NO_WINDOW = 0x08000000
TASK_NAME = "PC Booster - Pulizia automatica"


# --------------------------------------------------------------------------
# Utilità
# --------------------------------------------------------------------------
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
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, " ".join(sys.argv[1:])
    else:
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def parse_date(value) -> str:
    """Date di WMI ('/Date(…)/', '20240315…') o del registro ('12-5-2025') → AAAA-MM-GG."""
    if not value:
        return ""
    s = str(value).strip()
    if s.startswith("/Date("):
        try:
            ms = int(s[6:].split(")")[0].split("+")[0].lstrip("-").split("-")[0])
            return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    parts = s.replace("/", "-").split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        if len(parts[0]) == 4:
            y, m, d = parts
        else:
            m, d, y = parts  # formato del registro: mese-giorno-anno
        try:
            return datetime.date(int(y), int(m), int(d)).isoformat()
        except ValueError:
            return ""
    return s[:10]


def days_since(date_str: str):
    try:
        return (datetime.datetime.now() - datetime.datetime.strptime(date_str, "%Y-%m-%d")).days
    except ValueError:
        return None


def vendor_of(name: str) -> str:
    n = (name or "").lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "gtx" in n or "quadro" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n or "ryzen" in n:
        return "amd"
    if "intel" in n or " arc" in n:
        return "intel"
    return "altro"


def nvidia_friendly_version(wmi_version: str) -> str:
    """'32.0.15.7680' (versione di Windows) → '576.80' (versione NVIDIA)."""
    parts = (wmi_version or "").split(".")
    if len(parts) < 4:
        return wmi_version or ""
    digits = (parts[-2] + parts[-1])[-5:]
    return f"{digits[:3]}.{digits[3:]}" if len(digits) == 5 else wmi_version


DRIVER_PAGES = {
    "nvidia": "https://www.nvidia.com/it-it/drivers/",
    "amd": "https://www.amd.com/it/support/download/drivers.html",
    "intel": "https://www.intel.it/content/www/it/it/support/detect.html",
}


# --------------------------------------------------------------------------
# Dati di esempio (solo fuori da Windows, per vedere l'interfaccia)
# --------------------------------------------------------------------------
DEMO = {
    "system": {"os": "Windows 11 Pro 24H2 (build 26100)", "cpu": "AMD Ryzen 7 5800X 8-Core Processor",
               "cores": 8, "threads": 16, "ram_gb": 32, "ram_detail": "2 × 16 GB · 3600 MT/s",
               "gpu": "NVIDIA GeForce RTX 3070", "pc_name": "PC-MATTEO", "uptime": "3 h 12 min", "admin": True},
    "gpus": [
        {"name": "NVIDIA GeForce RTX 3070", "vendor": "nvidia", "kind": "Dedicata", "driver_version": "576.80",
         "driver_raw": "32.0.15.7680", "driver_date": "2025-06-10", "age_days": 130, "vram": "8 GB",
         "display": "2560 × 1440 a 165 Hz", "status": "ok", "download_page": DRIVER_PAGES["nvidia"], "live": True},
        {"name": "AMD Radeon(TM) Graphics", "vendor": "amd", "kind": "Integrata", "driver_version": "Adrenalin 24.9.1",
         "driver_raw": "31.0.21912.14", "driver_date": "2024-09-18", "age_days": 395, "vram": "Condivisa",
         "display": "", "status": "warn", "download_page": DRIVER_PAGES["amd"], "live": False},
    ],
    "drives": [
        {"letter": "C:", "label": "Sistema", "kind": "SSD NVMe", "model": "Samsung SSD 980 1TB",
         "total_gb": 931, "free_gb": 98, "used_pct": 89},
        {"letter": "D:", "label": "Giochi", "kind": "SSD", "model": "Crucial MX500 2TB",
         "total_gb": 1863, "free_gb": 612, "used_pct": 67},
    ],
}


# --------------------------------------------------------------------------
# API esposta all'interfaccia
# --------------------------------------------------------------------------
class Api:
    def __init__(self):
        self._apps_cache = []
        self._startup_cache = []
        self._lock = threading.Lock()
        self._cleaner = Cleaner()
        self._driver_dl = drivers.DriverDownloader()
        self._gpus = None

    # ================= Panoramica =================
    def get_system_info(self):
        if not IS_WINDOWS:
            r = dict(DEMO["system"])
            r.update(self.get_live_stats())
            return r
        info = run_ps_json(
            "$os=Get-CimInstance Win32_OperatingSystem;"
            "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;"
            "$mem=Get-CimInstance Win32_PhysicalMemory;"
            "$disp=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion' -ErrorAction SilentlyContinue).DisplayVersion;"
            "[pscustomobject]@{os=$os.Caption; build=$os.BuildNumber; ver=$disp; cpu=$cpu.Name.Trim();"
            "cores=$cpu.NumberOfCores; threads=$cpu.NumberOfLogicalProcessors;"
            "ramBytes=($mem | Measure-Object Capacity -Sum).Sum; sticks=@($mem).Count;"
            "speed=($mem | Measure-Object ConfiguredClockSpeed -Maximum).Maximum; pc=$env:COMPUTERNAME}"
        )
        i = info[0] if info else {}
        ram_bytes = i.get("ramBytes") or 0
        ram_gb = round(ram_bytes / 1024 ** 3) if ram_bytes else 0
        sticks = i.get("sticks") or 0
        detail = []
        if sticks and ram_gb:
            per = ram_gb / sticks
            detail.append(f"{sticks} × {per:.0f} GB" if per == int(per) else f"{sticks} moduli")
        if i.get("speed"):
            detail.append(f"{i['speed']} MT/s")
        gpus = self.get_gpu_info()
        main_gpu = next((g for g in gpus if g["kind"] == "Dedicata"), gpus[0] if gpus else None)
        os_name = (i.get("os") or "Windows").replace("Microsoft ", "")
        result = {
            "os": f"{os_name} {i.get('ver') or ''} (build {i.get('build', '?')})".replace("  ", " "),
            "cpu": i.get("cpu", "?"), "cores": i.get("cores", 0), "threads": i.get("threads", 0),
            "ram_gb": ram_gb, "ram_detail": " · ".join(detail),
            "gpu": main_gpu["name"] if main_gpu else "Non rilevata", "pc_name": i.get("pc", ""),
            "uptime": self._uptime(), "admin": is_admin(),
        }
        result.update(self.get_live_stats())
        return result

    @staticmethod
    def _uptime():
        if psutil is None:
            return ""
        secs = int(datetime.datetime.now().timestamp() - psutil.boot_time())
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        return f"{d} g {h} h" if d else f"{h} h {m} min"

    def get_live_stats(self):
        if psutil is None:
            return {"cpu_load": 0, "ram_used": 0, "ram_used_gb": 0, "disk_free_gb": 0, "disk_total_gb": 0}
        drive = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"
        disk = psutil.disk_usage(drive)
        vm = psutil.virtual_memory()
        return {
            "cpu_load": round(psutil.cpu_percent(interval=0.4)),
            "ram_used": round(vm.percent), "ram_used_gb": round(vm.used / 1024 ** 3, 1),
            "disk_free_gb": round(disk.free / 1024 ** 3), "disk_total_gb": round(disk.total / 1024 ** 3),
        }

    def get_drives(self):
        if not IS_WINDOWS:
            return DEMO["drives"]
        kinds = {}
        for r in run_ps_json(
            "Get-Partition | Where-Object DriveLetter | ForEach-Object { $n=$_.DiskNumber;"
            "$pd=Get-PhysicalDisk | Where-Object DeviceId -eq \"$n\";"
            "[pscustomobject]@{l=\"$($_.DriveLetter)\"; m=\"$($pd.MediaType)\"; b=\"$($pd.BusType)\"; f=$pd.FriendlyName} }"
        ):
            media = r.get("m") or ""
            kind = "SSD" if media == "SSD" else "HDD" if media == "HDD" else "Disco"
            if r.get("b") == "NVMe":
                kind = "SSD NVMe"
            elif r.get("b") == "USB":
                kind = "USB"
            kinds[(r.get("l") or "").upper()] = (kind, r.get("f") or "")
        drives = []
        for part in psutil.disk_partitions(all=False) if psutil else []:
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            letter = part.device[:2].upper()
            kind, model = kinds.get(letter[0], ("Disco", ""))
            drives.append({"letter": letter, "label": "Sistema" if letter == os.environ.get("SystemDrive", "C:").upper() else "",
                           "kind": kind, "model": model, "total_gb": round(u.total / 1024 ** 3),
                           "free_gb": round(u.free / 1024 ** 3), "used_pct": round(u.percent)})
        return drives

    # ================= Scheda video =================
    def _display_registry(self):
        """Legge dal registro i dati veri delle schede video (memoria, versione, data)."""
        res = {}
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            return res
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(k, sub) as s:
                    def v(n):
                        try:
                            return winreg.QueryValueEx(s, n)[0]
                        except OSError:
                            return None
                    mem = v("HardwareInformation.qwMemorySize")
                    if mem is None:
                        mem = v("HardwareInformation.MemorySize")
                    if isinstance(mem, bytes):
                        mem = int.from_bytes(mem[:8], "little")
                    desc = v("DriverDesc")
                    if desc:
                        res[desc.strip()] = {"mem": mem if isinstance(mem, int) else None,
                                             "version": v("DriverVersion"), "date": v("DriverDate"),
                                             "radeon": v("RadeonSoftwareVersion")}
            except OSError:
                continue
        winreg.CloseKey(k)
        return res

    @staticmethod
    def _nvidia_smi(query):
        smi = shutil.which("nvidia-smi")
        if not smi:
            return None
        try:
            out = subprocess.run([smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=CREATE_NO_WINDOW).stdout.strip().splitlines()
            return [[c.strip() for c in line.split(",")] for line in out if line.strip()]
        except Exception:
            return None

    def get_gpu_info(self):
        self._gpus = self._read_gpus()
        return self._gpus

    def _read_gpus(self):
        if not IS_WINDOWS:
            return DEMO["gpus"]
        rows = run_ps_json(
            "Get-CimInstance Win32_VideoController | Where-Object {$_.Name -notmatch 'Basic Display|Remote|Virtual|Mirage|Parsec|Meta'} "
            "| Select-Object Name, DriverVersion, DriverDate, AdapterRAM, CurrentHorizontalResolution, "
            "CurrentVerticalResolution, CurrentRefreshRate"
        )
        reg = self._display_registry()
        smi = self._nvidia_smi("name,driver_version,memory.total")
        gpus = []
        for r in rows:
            name = (r.get("Name") or "").strip()
            vendor = vendor_of(name)
            info = reg.get(name, {})
            raw = r.get("DriverVersion") or info.get("version") or ""
            date = parse_date(info.get("date")) or parse_date(r.get("DriverDate"))
            # Memoria: WMI si ferma a 4 GB, il registro ha il valore vero
            mem = info.get("mem") or 0
            if not mem:
                ar = r.get("AdapterRAM") or 0
                mem = ar if ar and ar > 0 else 0
            # Versione come la mostra il produttore
            version = raw
            if vendor == "nvidia":
                version = nvidia_friendly_version(raw)
                if smi:
                    match = next((s for s in smi if s and s[0] in name or name in s[0]), smi[0])
                    version = match[1] if len(match) > 1 else version
                    try:
                        mem = int(float(match[2])) * 1024 * 1024
                    except (ValueError, IndexError):
                        pass
            elif vendor == "amd" and info.get("radeon"):
                version = f"Adrenalin {info['radeon']}"
            n = name.lower()
            integrated = (vendor == "intel" and "arc" not in n) or \
                         (vendor == "amd" and n.replace("(tm)", "").replace("  ", " ").strip() in ("amd radeon graphics",)) or \
                         ("vega" in n and "rx" not in n) or \
                         (vendor == "amd" and any(x in n for x in ("610m", "660m", "680m", "740m", "760m",
                                                                    "780m", "880m", "890m", "8060s")))
            if integrated:
                vram = "Condivisa"
            elif mem:
                gb = mem / 1024 ** 3
                vram = f"{gb:.0f} GB" if gb >= 1 else f"{mem / 1024 ** 2:.0f} MB"
            else:
                vram = "n.d."
            hres, vres, hz = r.get("CurrentHorizontalResolution"), r.get("CurrentVerticalResolution"), r.get("CurrentRefreshRate")
            display = f"{hres} × {vres} a {hz} Hz" if hres and vres and hz else ""
            age = days_since(date)
            status = "unknown" if age is None else ("warn" if age > 180 else "ok")
            gpus.append({
                "name": name, "vendor": vendor, "kind": "Integrata" if integrated else "Dedicata",
                "driver_version": version, "driver_raw": raw, "driver_date": date, "age_days": age,
                "vram": vram, "display": display, "status": status,
                "download_page": DRIVER_PAGES.get(vendor, ""), "live": vendor == "nvidia" and bool(smi),
            })
        gpus.sort(key=lambda g: g["kind"] != "Dedicata")
        return gpus

    # ---------- ultimo driver (NVIDIA / AMD) ----------
    def check_gpu_driver(self, name):
        gpus = self._gpus or self.get_gpu_info()
        gpu = next((g for g in gpus if g["name"] == name), None)
        if not gpu:
            return {"ok": False, "msg": "Scheda video non trovata."}
        if not IS_WINDOWS:
            if gpu["vendor"] == "nvidia":
                return {"ok": True, "version": "581.57", "installed": gpu["driver_version"], "update": True,
                        "date": "Tue Oct 14, 2026", "name": "GeForce Game Ready Driver", "size": "866.3 MB",
                        "url": "https://us.download.nvidia.com/Windows/581.57/581.57-desktop-win10-win11-64bit-international-dch-whql.exe"}
            return {"ok": True, "version": "26.8.1", "installed": "24.9.1", "update": True, "date": "2026-08-20",
                    "name": "AMD Software: Adrenalin Edition", "size": "",
                    "url": "https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.8.1-win11-c.exe"}
        return drivers.check_latest(gpu)

    def download_driver(self, url, vendor):
        if not IS_WINDOWS:
            import time as _t
            def fake():
                self._driver_dl.state = {"status": "downloading", "done": 0, "total": 866_000_000}
                for i in range(1, 11):
                    _t.sleep(0.35)
                    self._driver_dl.state["done"] = 86_600_000 * i
                self._driver_dl.state = {"status": "launched", "msg": "Demo: installazione avviata."}
            threading.Thread(target=fake, daemon=True).start()
            return {"ok": True}
        return self._driver_dl.start(url, vendor)

    def driver_progress(self):
        return self._driver_dl.progress()

    # ---------- aggiornamento app (winget) ----------
    def list_app_updates(self):
        return apptools.list_app_updates()

    def upgrade_app(self, pkg_id):
        return apptools.upgrade_app(pkg_id)

    def open_winget_store(self):
        return apptools.open_winget_store()

    def running_apps(self):
        return apptools.running_apps()

    def get_gpu_live(self):
        """Temperatura, utilizzo e memoria della scheda video in tempo reale."""
        if not IS_WINDOWS:
            import random
            return {"ok": True, "temp": random.randint(48, 66), "load": random.randint(3, 40),
                    "mem_used": round(random.uniform(1.2, 3.4), 1), "mem_total": 8, "power": random.randint(30, 120)}
        data = self._nvidia_smi("temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw")
        if data:
            d = data[0]
            try:
                return {"ok": True, "temp": int(float(d[0])), "load": int(float(d[1])),
                        "mem_used": round(float(d[2]) / 1024, 1), "mem_total": round(float(d[3]) / 1024),
                        "power": int(float(d[4])) if d[4] not in ("[N/A]", "") else None}
            except (ValueError, IndexError):
                pass
        out = run_ps("((Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage' -ErrorAction SilentlyContinue)"
                     ".CounterSamples | Measure-Object CookedValue -Sum).Sum", 15)
        try:
            return {"ok": True, "temp": None, "load": min(100, round(float(out or 0))), "mem_used": None,
                    "mem_total": None, "power": None}
        except ValueError:
            return {"ok": False}

    def get_gpu_tweaks(self):
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
        return {"power_plan": name, "high_perf": "8c5e7fda" in out or "e9a42b02" in out}

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
        if isinstance(url, str) and url.startswith("https://"):
            if IS_WINDOWS:
                os.startfile(url)
            return True
        return False

    def open_vendor_app(self, vendor):
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
                return {"ok": True, "msg": "App del produttore aperta: da lì installi l'ultimo driver."}
        return {"ok": False, "msg": "App del produttore non trovata: apro il sito ufficiale."}

    # ================= Ottimizza tutto =================
    OPTIMAL = [("game_mode", True), ("hags", True), ("high_perf", True), ("game_dvr", False)]

    def optimize_all(self, restore_point=True):
        notes = []
        if restore_point:
            rp = self.create_restore_point()
            if not rp["ok"]:
                notes.append("Punto di ripristino non creato.")
        results = [self.set_gpu_tweak(name, value) for name, value in self.OPTIMAL]
        done = sum(1 for r in results if r["ok"])
        failed = [r["msg"] for r in results if not r["ok"]]
        msg = f"{done} ottimizzazioni su {len(results)} applicate."
        msg += (" Non riuscite: " + " ".join(failed)) if failed else " Riavvia il PC per attivarle tutte."
        if notes:
            msg += " " + " ".join(notes)
        return {"ok": done > 0, "msg": msg, "done": done, "total": len(results)}

    # ================= Sicurezza =================
    def create_restore_point(self):
        """Crea un punto di ripristino di Windows (per tornare indietro se qualcosa va storto)."""
        if not IS_WINDOWS:
            return {"ok": True, "msg": "Demo: punto di ripristino creato."}
        if not is_admin():
            return {"ok": False, "msg": "Servono i permessi di amministratore."}
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        # Windows di norma permette un punto ogni 24 ore: togliamo il limite per questa operazione
        run_ps("New-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore' "
               "-Name SystemRestorePointCreationFrequency -Value 0 -PropertyType DWord -Force | Out-Null;"
               f"Enable-ComputerRestore -Drive '{drive}' -ErrorAction SilentlyContinue", 60)
        out = run_ps("try { Checkpoint-Computer -Description 'PC Booster' -RestorePointType MODIFY_SETTINGS "
                     "-ErrorAction Stop; 'OK' } catch { $_.Exception.Message }", 300)
        if out.strip().endswith("OK"):
            return {"ok": True, "msg": "Punto di ripristino creato."}
        return {"ok": False, "msg": "Non sono riuscito a creare il punto di ripristino. "
                                    "Controlla che la Protezione sistema sia attiva."}

    def open_system_restore(self):
        if IS_WINDOWS:
            subprocess.Popen(["rstrui.exe"])
        return True

    # ================= Pulizia =================
    def scan_junk(self):
        return self._cleaner.scan()

    def clean_junk(self, ids):
        return self._cleaner.clean(ids)

    def find_large_files(self, min_mb=250):
        return self._cleaner.find_large_files(int(min_mb))

    def reveal_file(self, item_id):
        return self._cleaner.reveal_file(item_id)

    def recycle_file(self, item_id):
        return self._cleaner.recycle_file(item_id)

    # ---------- pulizia automatica (Utilità di pianificazione di Windows) ----------
    def get_auto_clean(self):
        if not IS_WINDOWS:
            return {"freq": getattr(self, "_demo_freq", "off"), "available": True}
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], capture_output=True, text=True,
                           errors="replace", creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return {"freq": "off", "available": getattr(sys, "frozen", False)}
        xml = r.stdout
        freq = "daily" if "<ScheduleByDay>" in xml else "monthly" if "<ScheduleByMonth>" in xml else "weekly"
        return {"freq": freq, "available": True}

    def set_auto_clean(self, freq):
        labels = {"off": "disattivata", "daily": "ogni giorno", "weekly": "ogni settimana", "monthly": "ogni mese"}
        if freq not in labels:
            return {"ok": False, "msg": "Frequenza non valida."}
        if not IS_WINDOWS:
            self._demo_freq = freq
            return {"ok": True, "msg": f"Pulizia automatica {labels[freq]}."}
        if freq == "off":
            subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
            return {"ok": True, "msg": "Pulizia automatica disattivata."}
        if not getattr(sys, "frozen", False):
            return {"ok": False, "msg": "Disponibile solo nella versione .exe."}
        sched = {"daily": ["/SC", "DAILY"], "weekly": ["/SC", "WEEKLY", "/D", "SUN"],
                 "monthly": ["/SC", "MONTHLY", "/D", "1"]}[freq]
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{sys.executable}" --auto-clean',
                            *sched, "/ST", "12:00", "/RL", "HIGHEST", "/F"],
                           capture_output=True, text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return {"ok": False, "msg": "Non riesco a programmare la pulizia: servono i permessi di amministratore."}
        return {"ok": True, "msg": f"Pulizia automatica {labels[freq]}, a mezzogiorno."}

    def auto_clean(self):
        """Chiamata dall'attività pianificata: pulisce le voci sicure, senza finestra."""
        scan = self._cleaner.scan()
        ids = [i["id"] for i in scan["items"] if i["default"] and i["id"] not in ("recycle",)]
        return self._cleaner.clean(ids)

    # ================= Aggiornamenti di Windows =================
    def check_updates(self):
        if not IS_WINDOWS:
            return {"ok": True, "updates": [
                {"title": "NVIDIA - Display - 32.0.15.8129", "type": "Driver", "category": "gpu", "size": "812.4 MB", "id": "a"},
                {"title": "AMD - System - 2.13.27.501 (chipset)", "type": "Driver", "category": "cpu", "size": "1.2 MB", "id": "b"},
                {"title": "2026-10 Aggiornamento cumulativo per Windows 11 (KB5066001)", "type": "Software",
                 "category": "system", "size": "640.0 MB", "id": "c"}]}
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
            if cls == "display" or " display " in f" {t} ":
                cat = "gpu"
            elif cls in ("system", "processor") or "chipset" in t or "processor" in t or "microcode" in t:
                cat = "cpu"
            else:
                cat = "system"
            updates.append({"title": title, "type": r.get("type"), "category": cat,
                            "size": human_size(r.get("size") or 0), "id": r.get("id")})
        return {"ok": True, "updates": updates}

    def install_updates(self, ids):
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
        ok = r.get("code") in (2, 3)
        msg = f"Installati {r.get('n', 0)} aggiornamenti." if ok else "Installazione non riuscita."
        if r.get("reboot"):
            msg += " Riavvia il PC per completare."
        return {"ok": ok, "msg": msg, "reboot": bool(r.get("reboot"))}

    def get_cpu_info(self):
        if not IS_WINDOWS:
            return {"name": "AMD Ryzen 7 5800X 8-Core Processor", "vendor": "amd", "base_mhz": 3801, "cores": 8,
                    "threads": 16, "cache": "32 MB", "socket": "AM4", "bios": "2025-03-11", "bios_version": "F37",
                    "board": "Gigabyte B550 AORUS ELITE", "chipset_page": DRIVER_PAGES["amd"]}
        rows = run_ps_json(
            "$c=Get-CimInstance Win32_Processor | Select-Object -First 1; $b=Get-CimInstance Win32_BIOS;"
            "$m=Get-CimInstance Win32_BaseBoard;"
            "[pscustomobject]@{name=$c.Name.Trim(); mhz=$c.MaxClockSpeed; cores=$c.NumberOfCores;"
            "threads=$c.NumberOfLogicalProcessors; l3=$c.L3CacheSize; socket=$c.SocketDesignation;"
            "bios=$b.ReleaseDate; biosver=$b.SMBIOSBIOSVersion; board=\"$($m.Manufacturer) $($m.Product)\"}"
        )
        r = rows[0] if rows else {}
        vendor = vendor_of(r.get("name", ""))
        l3 = r.get("l3") or 0
        return {"name": r.get("name", "?"), "vendor": vendor, "base_mhz": r.get("mhz"), "cores": r.get("cores"),
                "threads": r.get("threads"), "cache": f"{round(l3 / 1024)} MB" if l3 else "",
                "socket": r.get("socket") or "", "bios": parse_date(r.get("bios")),
                "bios_version": r.get("biosver", ""), "board": (r.get("board") or "").strip(),
                "chipset_page": {"intel": DRIVER_PAGES["intel"], "amd": DRIVER_PAGES["amd"]}.get(vendor, "")}

    # ================= Avvio automatico =================
    STARTUP_SUGGEST_OFF = ("discord", "steam", "epic", "spotify", "teams", "skype", "battle.net", "ubisoft",
                           "uplay", "ea desktop", "eadesktop", "origin", "gog galaxy", "riot", "opera", "edge",
                           "msedge", "ccleaner", "adobe", "acrobat", "creative cloud", "itunes", "zoom",
                           "whatsapp", "telegram", "dropbox", "google drive", "utorrent", "qbittorrent",
                           "cortana", "xbox", "rockstar", "wallpaper engine", "icue", "razer", "logitech", "lghub")
    STARTUP_KEEP = ("securityhealth", "windows defender", "realtek", "nvidia", "amd", "radeon", "intel",
                    "synaptics", "elan", "onedrive", "audio", "bluetooth", "touchpad", "pc booster")
    _APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

    def _startup_sources(self):
        env = os.environ
        hkcu, hklm = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
        run = r"Software\Microsoft\Windows\CurrentVersion\Run"
        return [
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
            if not self._startup_cache:
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
                entries = [(f, os.path.join(path, f)) for f in os.listdir(path) if f.lower() != "desktop.ini"]
            for name, cmd in entries:
                enabled = True
                try:
                    with winreg.OpenKey(aroot, rf"{self._APPROVED}\{approved}") as ak:
                        data = winreg.QueryValueEx(ak, name)[0]
                        if isinstance(data, bytes) and data and data[0] & 1:
                            enabled = False
                except OSError:
                    pass
                label = os.path.splitext(name)[0] if kind == "dir" else name
                items.append({"id": str(len(items)), "name": label, "command": cmd, "enabled": enabled,
                              "where": where, "suggest": self._suggestion(label, cmd),
                              "_value": name, "_approved": approved, "_aroot": aroot})
        items.sort(key=lambda x: (x["suggest"] != "off", x["name"].lower()))
        for i, it in enumerate(items):
            it["id"] = str(i)
        self._startup_cache = items
        return [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]

    def set_startup(self, item_id, enabled):
        item = next((x for x in self._startup_cache if x["id"] == item_id), None)
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
        if not self._startup_cache:
            self.list_startup()
        targets = [x for x in self._startup_cache if x["enabled"] and x["suggest"] == "off"]
        if not targets:
            return {"ok": True, "msg": "Niente da disattivare: l'avvio è già pulito."}
        ok = [x["name"] for x in targets if self.set_startup(x["id"], False)["ok"]]
        return {"ok": bool(ok), "msg": f"Tolti dall'avvio: {', '.join(ok)}." if ok else "Nessun programma disattivato."}

    # ================= App e giochi =================
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
                {"id": "a7", "name": "McAfee WebAdvisor", "publisher": "McAfee, LLC", "size_mb": 64, "date": "2023-01-15", "game": False},
                {"id": "a6", "name": "WinRAR 7.01", "publisher": "win.rar GmbH", "size_mb": 9, "date": "2024-06-03", "game": False},
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
        app = next((a for a in self._apps_cache if a["id"] == app_id), None)
        if not app:
            return {"ok": False, "msg": "App non trovata, aggiorna l'elenco."}
        if not IS_WINDOWS:
            return {"ok": True, "msg": f"Demo: avrei avviato la disinstallazione di {app['name']}."}
        cmd = app["_uninstall"]
        try:
            if "msiexec" in cmd.lower():
                cmd = cmd.replace("/I", "/X").replace("/i", "/X")
            subprocess.Popen(cmd, shell=True)
            return {"ok": True, "msg": f"Disinstallazione di {app['name']} avviata: segui la finestra che si è aperta."}
        except Exception as e:
            return {"ok": False, "msg": f"Impossibile avviare la disinstallazione: {e}"}

    def open_windows_settings(self, page):
        allowed = {"apps": "ms-settings:appsfeatures", "update": "ms-settings:windowsupdate",
                   "storage": "ms-settings:storagesense", "graphics": "ms-settings:display-advancedgraphics",
                   "protection": "ms-settings:about"}
        if IS_WINDOWS and page in allowed:
            os.startfile(allowed[page])
        return True

    def restart_as_admin(self):
        return relaunch_as_admin()
