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
import re
import subprocess
import threading

from cleaner import Cleaner, human_size
import drivers
import apptools
import security
import history
import network
import diagnostics
import winutil

IS_WINDOWS = sys.platform == "win32"
WIN11 = IS_WINDOWS and sys.getwindowsversion().build >= 22000

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
    """Esegue uno script PowerShell senza aprire finestre e restituisce l'output ("" se non va)."""
    return winutil.ps(script, timeout)


def run_ps_json(script: str, timeout: int = 300):
    """Come run_ps, ma legge il JSON e restituisce sempre una lista."""
    return [x for x in winutil.as_list(winutil.ps_json(script, timeout)) if isinstance(x, dict)]


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


WU_ERRORS = {
    "0x8024402C": "Windows Update non riesce a collegarsi: controlla la connessione a internet.",
    "0x80072EE2": "Windows Update non risponde (tempo scaduto): controlla la connessione e riprova.",
    "0x80072EE7": "Windows Update non riesce a collegarsi: controlla la connessione a internet.",
    "0x80072EFD": "Windows Update non riesce a collegarsi: controlla la connessione a internet.",
    "0x80072F8F": "Data o ora del PC sbagliate: sistemale e riprova.",
    "0x80240438": "Windows Update non riesce a collegarsi: controlla la connessione a internet.",
    "0x8024A105": "Windows Update è occupato o va riavviato: riavvia il PC e riprova.",
    "0x80240016": "Windows Update sta già installando qualcosa: aspetta che finisca e riprova.",
    "0x80070422": "Il servizio Windows Update è disattivato.",
    "0x80070005": "Permesso negato: servono i permessi di amministratore.",
}


def wu_error_text(code):
    code = (code or "").upper().replace("0X", "0x")
    return WU_ERRORS.get(code, f"Windows Update ha dato un errore ({code or 'sconosciuto'}). Riprova tra poco.")


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
        self._sec = security.Security()
        self._speed = network.SpeedTest()
        self._undo_lock = threading.Lock()

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
        r = winutil.run([smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"], timeout=8,
                        log_errors=False)
        if not r.ok:
            return None
        rows = [[c.strip() for c in line.split(",")] for line in r.out.strip().splitlines() if line.strip()]
        return rows or None

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
                self._driver_dl.state = {"status": "launched", "msg": "Demo: installazione in corso…"}
                _t.sleep(3)
                self._driver_dl.state = {"status": "finished", "msg": "Installazione finita: rileggo il driver installato…"}
            threading.Thread(target=fake, daemon=True).start()
            return {"ok": True}
        r = self._driver_dl.start(url, vendor)
        if r.get("ok"):
            history.add("driver", f"Download del driver {vendor.upper()} avviato", detail=url.rsplit('/', 1)[-1])
        return r

    def driver_progress(self):
        return self._driver_dl.progress()

    # ---------- sicurezza (Microsoft Defender) ----------
    def security_status(self):
        return self._sec.status()

    def security_threats(self):
        return self._sec.threats()

    def security_scan(self, kind):
        return self._sec.start_scan(kind)

    def security_progress(self):
        return self._sec.scan_progress()

    def security_cancel(self):
        return self._sec.cancel_scan()

    def security_update(self):
        return self._sec.update_signatures()

    def security_remove(self):
        r = self._sec.remove_threats()
        if r.get("ok"):
            history.add("security", "Sicurezza: minacce rimosse")
        return r

    def security_offline(self):
        return self._sec.offline_scan()

    def security_realtime(self):
        r = self._sec.enable_realtime()
        if r.get("ok"):
            history.add("security", "Sicurezza: protezione in tempo reale attivata")
        return r

    def security_firewall(self):
        r = self._sec.enable_firewall()
        if r.get("ok"):
            history.add("security", "Sicurezza: firewall attivato")
        return r

    def open_windows_security(self):
        return self._sec.open_windows_security()

    # ---------- salute dei dischi ----------
    def disk_health(self):
        return security.disk_health()

    # ---------- aggiornamento app (winget) ----------
    def list_app_updates(self):
        return apptools.list_app_updates()

    def upgrade_app(self, pkg_id):
        r = apptools.upgrade_app(pkg_id)
        if r.get("ok"):
            history.add("apps", f"Aggiornato: {pkg_id}")
        return r

    # ---------- installa app (catalogo) ----------
    def app_catalog(self):
        return apptools.app_catalog()

    def install_app(self, pkg_id, name=""):
        r = apptools.install_app(pkg_id)
        if r.get("ok"):
            history.add("apps", f"Installato: {name or pkg_id}")
        return r

    # ---------- rete ----------
    def network_info(self):
        return network.connection_info()

    def network_ping(self):
        return network.ping_all()

    def speedtest_start(self):
        return self._speed.start()

    def speedtest_progress(self):
        return self._speed.progress()

    def dns_benchmark(self):
        info = network.connection_info()
        current = next((a["dns"] for a in info.get("adapters", []) if a.get("dns")), None)
        return network.benchmark_dns(current)

    def dns_set(self, provider_id):
        r = network.set_dns(provider_id)
        if r.get("ok"):
            name = "automatici" if provider_id == "auto" else next(
                (p["name"] for p in network.DNS_PROVIDERS if p["id"] == provider_id), provider_id)
            history.add("network", f"DNS cambiati: {name}", undo=r.get("undo"))
        return {k: v for k, v in r.items() if k != "undo"}

    # ---------- cronologia ----------
    def history_list(self):
        return list(reversed(history.load()))

    def history_clear(self):
        history.clear()
        return {"ok": True, "msg": "Cronologia svuotata."}

    def undo_change(self, entry_id):
        with self._undo_lock:      # due clic veloci su "Annulla" non annullano due volte
            return self._undo_change(entry_id)

    def _undo_change(self, entry_id):
        e = history.get(entry_id)
        if not e or not e.get("undo") or e.get("undone"):
            return {"ok": False, "msg": "Questa modifica non si può annullare."}
        u = e["undo"]
        t = u.get("type")
        try:
            if t == "tweak":
                r = self.set_gpu_tweak(u["name"], u["value"], record=False)
            elif t == "plan":
                ok = apptools._set_plan(u["guid"])
                r = {"ok": ok, "msg": "Piano energetico ripristinato." if ok else "Non riesco a ripristinare il piano energetico."}
            elif t == "startup":
                if not IS_WINDOWS:
                    item = next((x for x in self._startup_cache if x["id"] == u.get("demo_id")), None)
                    if item:
                        item["enabled"] = u["enabled"]
                else:
                    root = winreg.HKEY_LOCAL_MACHINE if u["root"] == "HKLM" else winreg.HKEY_CURRENT_USER
                    self._write_startup(root, u["approved"], u["value"], u["enabled"])
                    self._startup_cache = []
                r = {"ok": True, "msg": f"{u['name']} " + ("di nuovo all'avvio." if u["enabled"] else "tolto dall'avvio.")}
            elif t == "dns":
                r = network.restore_dns(u.get("prev"))
            elif t == "hibernate":
                ok = not IS_WINDOWS or winutil.run(["powercfg", "/h", "on"], timeout=60).ok
                r = {"ok": ok, "msg": "Ibernazione riattivata." if ok else "Non riesco a riattivare l'ibernazione."}
            else:
                r = {"ok": False, "msg": "Tipo di modifica sconosciuto."}
        except PermissionError:
            r = {"ok": False, "msg": "Permesso negato: servono i permessi di amministratore."}
        if r.get("ok"):
            history.mark_undone(entry_id)
            history.add(e["kind"], f"Annullato: {e['label']}")
        return r

    # ---------- segnala un problema ----------
    def make_report(self):
        try:
            return diagnostics.make_report(self.get_system_info())
        except Exception as ex:
            return {"ok": False, "msg": f"Non riesco a creare il report: {ex}"}

    def open_issue(self):
        url = diagnostics.issue_url(self.get_system_info())
        if IS_WINDOWS:
            os.startfile(url)
        return {"ok": True, "url": url}

    def log_ui_error(self, text):
        diagnostics.log_error(f"[interfaccia] {str(text)[:4000]}")
        return True

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
                    "game_dvr": True, "mouse_raw": getattr(self, "_demo_mouse", False),
                    "windowed_opt": getattr(self, "_demo_windowed", False)}
        return {
            "mouse_raw": self._mouse_accel() == 0,
            "windowed_opt": self._windowed_opt() if WIN11 else None,
            "game_mode": self._reg_get(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar",
                                       "AutoGameModeEnabled", 1) == 1,
            "hags": self._reg_get(winreg.HKEY_LOCAL_MACHINE,
                                  r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 1) == 2,
            "game_dvr": self._reg_get(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore",
                                      "GameDVR_Enabled", 1) == 1,
            **self._power_plan(),
        }

    TWEAK_LABELS = {"game_mode": "Modalità Gioco", "hags": "Pianificazione GPU con accelerazione hardware",
                    "high_perf": "Prestazioni elevate", "game_dvr": "Registrazione in background",
                    "mouse_raw": "Mouse senza accelerazione", "windowed_opt": "Ottimizzazioni per i giochi in finestra"}

    # ---------- mouse: "Migliora precisione puntatore" (accelerazione) ----------
    @staticmethod
    def _mouse_accel():
        arr = (ctypes.c_int * 3)()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0003, 0, arr, 0):     # SPI_GETMOUSE
            return None
        return arr[2]

    @staticmethod
    def _set_mouse_accel(on):
        # valori predefiniti di Windows: soglie 6 e 10, accelerazione 1
        arr = (ctypes.c_int * 3)(*((6, 10, 1) if on else (0, 0, 0)))
        # SPI_SETMOUSE, salvato nel profilo (SPIF_UPDATEINIFILE) e comunicato alle app (SPIF_SENDCHANGE)
        return bool(ctypes.windll.user32.SystemParametersInfoW(0x0004, 0, arr, 0x01 | 0x02))

    # ---------- giochi in finestra (Windows 11) ----------
    _DX_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"

    def _dx_settings(self):
        raw = self._reg_get(winreg.HKEY_CURRENT_USER, self._DX_KEY, "DirectXUserGlobalSettings", "") or ""
        out = {}
        for part in str(raw).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _windowed_opt(self):
        return self._dx_settings().get("SwapEffectUpgradeEnable") == "1"

    def _set_windowed_opt(self, on):
        d = self._dx_settings()        # le altre impostazioni di DirectX restano com'erano
        d["SwapEffectUpgradeEnable"] = "1" if on else "0"
        text = "".join(f"{k}={v};" for k, v in d.items())
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._DX_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DirectXUserGlobalSettings", 0, winreg.REG_SZ, text)

    def set_gpu_tweak(self, name, enabled, record=True):
        undo = None
        if record and name in self.TWEAK_LABELS:
            if name == "high_perf":
                undo = {"type": "plan", "guid": apptools._active_plan()}
            else:
                undo = {"type": "tweak", "name": name, "value": bool(self.get_gpu_tweaks().get(name))}
        r = self._apply_tweak(name, enabled)
        if r.get("ok") and undo:
            history.add("optimize", f"{self.TWEAK_LABELS[name]}: {'attivata' if enabled else 'disattivata'}", undo=undo)
        return r

    def _apply_tweak(self, name, enabled):
        if not IS_WINDOWS:
            if name == "mouse_raw":
                self._demo_mouse = bool(enabled)
            if name == "windowed_opt":
                self._demo_windowed = bool(enabled)
            return {"ok": True, "msg": "Modalità demo: nessuna modifica fatta."}
        try:
            if name == "mouse_raw":
                if not self._set_mouse_accel(not enabled):
                    return {"ok": False, "msg": "Non riesco a cambiare l'impostazione del mouse."}
                return {"ok": True, "msg": "Accelerazione del mouse " + ("spenta: mira più precisa." if enabled else "riaccesa.")}
            if name == "windowed_opt":
                if not WIN11:
                    return {"ok": False, "msg": "Questa opzione c'è solo su Windows 11."}
                self._set_windowed_opt(bool(enabled))
                return {"ok": True, "msg": "Ottimizzazioni per i giochi in finestra " +
                                           ("attivate. Valgono dal prossimo avvio del gioco." if enabled else "disattivate.")}
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
                guid = apptools.HIGH_PERF if enabled else apptools.BALANCED
                if not apptools._set_plan(guid):
                    return {"ok": False, "msg": "Impossibile cambiare il piano energetico."}
                return {"ok": True, "msg": "Piano energetico: " + ("Prestazioni elevate." if enabled else "Bilanciato.")}
        except PermissionError:
            return {"ok": False, "msg": "Permesso negato: avvia l'app come amministratore."}
        except Exception as e:
            return {"ok": False, "msg": f"Errore: {e}"}
        return {"ok": False, "msg": "Opzione sconosciuta."}

    def _power_plan(self):
        out = winutil.run(["powercfg", "/getactivescheme"], timeout=15).out
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
    OPTIMAL = [("game_mode", True), ("hags", True), ("high_perf", True), ("game_dvr", False), ("windowed_opt", True)]

    def optimize_all(self, restore_point=True):
        notes = []
        if restore_point:
            rp = self.create_restore_point()
            if not rp["ok"]:
                notes.append("Punto di ripristino non creato.")
        current = self.get_gpu_tweaks()
        # salta le opzioni che questo PC non ha (es. giochi in finestra su Windows 10)
        results = [self.set_gpu_tweak(name, value) for name, value in self.OPTIMAL if current.get(name, False) is not None]
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
            history.add("security", "Punto di ripristino creato")
            return {"ok": True, "msg": "Punto di ripristino creato."}
        return {"ok": False, "msg": "Non sono riuscito a creare il punto di ripristino. "
                                    "Controlla che la Protezione sistema sia attiva."}

    def open_system_restore(self):
        if IS_WINDOWS:
            winutil.start(["rstrui"], hidden=False)
        return True

    # ================= Pulizia =================
    def scan_junk(self):
        return self._cleaner.scan()

    def clean_junk(self, ids):
        ids = [i for i in (ids or []) if isinstance(i, str)]
        r = self._cleaner.clean(ids)
        if r.get("ok"):
            history.add("clean", f"Pulizia: liberati {r.get('freed')}", detail=f"{len(ids)} voci pulite")
            if "hibernate" in ids and r.get("hibernate_off"):
                history.add("clean", "Ibernazione disattivata", detail="Libera lo spazio del file hiberfil.sys",
                            undo={"type": "hibernate"})
        return r

    def optimize_drives(self):
        """Ottimizza le unità come fa Windows: TRIM per gli SSD, deframmentazione per gli hard disk."""
        if not IS_WINDOWS:
            import time as _t
            _t.sleep(2)
            return {"ok": True, "msg": "Demo: C: ottimizzata (SSD), D: ottimizzata (SSD)."}
        if not is_admin():
            return {"ok": False, "msg": "Servono i permessi di amministratore."}
        r = winutil.ps_run(
            "$res=@(); Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' -and $_.FileSystem -eq 'NTFS' } |"
            " ForEach-Object { $l=$_.DriveLetter; try { Optimize-Volume -DriveLetter $l -ErrorAction Stop; $res += \"$($l):OK\" }"
            " catch { $res += \"$($l):ERR\" } }; $res -join ' '", 2 * 3600)
        parts = [x for x in r.out.split() if ":" in x]
        ok = [x.split(":")[0] + ":" for x in parts if x.endswith("OK")]
        bad = [x.split(":")[0] + ":" for x in parts if x.endswith("ERR")]
        if r.timed_out:
            return {"ok": bool(ok), "msg": "L'ottimizzazione ci mette molto: Windows la finirà da solo nei prossimi giorni."}
        if not ok:
            return {"ok": False, "msg": "Non sono riuscito a ottimizzare le unità."}
        history.add("disk", "Unità ottimizzate: " + " ".join(ok))
        msg = "Unità ottimizzate: " + " ".join(ok) + "."
        if bad:
            msg += " Non riuscite: " + " ".join(bad) + "."
        return {"ok": True, "msg": msg}

    def set_clean_choice(self, choice):
        """Ricorda le voci della pulizia che hai acceso o spento (vale anche per la pulizia automatica)."""
        import updater
        clean = {}
        for k, v in (choice or {}).items() if isinstance(choice, dict) else []:
            if isinstance(k, str) and re.fullmatch(r"[a-z0-9_]{1,40}", k) and isinstance(v, bool):
                clean[k] = v
        updater.save_settings({"clean_choice": clean})
        return {"ok": True}

    def find_large_files(self, min_mb=250):
        return self._cleaner.find_large_files(int(min_mb))

    def reveal_file(self, item_id):
        return self._cleaner.reveal_file(item_id)

    def recycle_file(self, item_id):
        r = self._cleaner.recycle_file(item_id)
        if r.get("ok"):
            history.add("disk", r["msg"], detail="Puoi recuperarlo dal Cestino finché non lo svuoti.")
        return r

    # ---------- pulizia automatica (Utilità di pianificazione di Windows) ----------
    def get_auto_clean(self):
        if not IS_WINDOWS:
            return {"freq": getattr(self, "_demo_freq", "off"), "available": True}
        r = winutil.run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], timeout=20, log_errors=False)
        if not r.ok:
            return {"freq": "off", "available": getattr(sys, "frozen", False)}
        xml = r.out
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
            winutil.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=20, log_errors=False)
            return {"ok": True, "msg": "Pulizia automatica disattivata."}
        if not getattr(sys, "frozen", False):
            return {"ok": False, "msg": "Disponibile solo nella versione .exe."}
        sched = {"daily": ["/SC", "DAILY"], "weekly": ["/SC", "WEEKLY", "/D", "SUN"],
                 "monthly": ["/SC", "MONTHLY", "/D", "1"]}[freq]
        r = winutil.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{sys.executable}" --auto-clean',
                         *sched, "/ST", "12:00", "/RL", "HIGHEST", "/F"], timeout=30)
        if not r.ok:
            return {"ok": False, "msg": "Non riesco a programmare la pulizia: servono i permessi di amministratore."}
        return {"ok": True, "msg": f"Pulizia automatica {labels[freq]}, a mezzogiorno."}

    def auto_clean(self):
        """Chiamata dall'attività pianificata: pulisce le voci sicure, senza finestra."""
        import updater
        choice = updater.load_settings().get("clean_choice") or {}
        scan = self._cleaner.scan()
        # le voci scelte da te; per le altre la scelta consigliata. Mai le operazioni pesanti o irreversibili.
        never = {"recycle", "dism", "winold", "hibernate"}
        ids = [i["id"] for i in scan["items"] if i["id"] not in never and choice.get(i["id"], i["default"])]
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
try {
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
  [pscustomobject]@{ ok=$true; list=@($list) }
} catch {
  [pscustomobject]@{ ok=$false; code=('0x{0:X8}' -f $_.Exception.HResult); err=$_.Exception.Message }
}
"""
        r = winutil.ps_run("& {" + script + "} | ConvertTo-Json -Depth 5 -Compress", timeout=600)
        if r.timed_out:
            return {"ok": False, "msg": "Windows Update non ha risposto in tempo. Riprova."}
        try:
            data = json.loads(r.out) if r.out else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            diagnostics.log_error(f"Windows Update: risposta non valida: {r.out[:300]} {r.err[:300]}")
            return {"ok": False, "msg": "Windows Update non risponde. Riprova tra poco."}
        if not data.get("ok"):
            diagnostics.log_error(f"Windows Update: {data.get('code')} {data.get('err')}")
            return {"ok": False, "msg": wu_error_text(data.get("code"))}
        updates = []
        for r in winutil.as_list(data.get("list")):
            if not isinstance(r, dict):
                continue
            title = r.get("title") or ""
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
        ids = [str(i) for i in (ids or []) if re.fullmatch(r"[0-9A-Za-z\-]{1,64}", str(i))]
        if not ids:
            return {"ok": False, "msg": "Nessun aggiornamento selezionato."}
        id_list = ",".join(f"'{i}'" for i in ids)
        script = rf"""
try {{
  $want = @({id_list})
  $s = New-Object -ComObject Microsoft.Update.Session
  $r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
  $col = New-Object -ComObject Microsoft.Update.UpdateColl
  foreach ($u in $r.Updates) {{ if ($want -contains $u.Identity.UpdateID) {{ if (-not $u.EulaAccepted) {{ $u.AcceptEula() }}; [void]$col.Add($u) }} }}
  if ($col.Count -eq 0) {{ [pscustomobject]@{{ok=$true; n=0; code=2; reboot=$false}}; return }}
  $d = $s.CreateUpdateDownloader(); $d.Updates = $col; [void]$d.Download()
  $i = $s.CreateUpdateInstaller(); $i.Updates = $col; $res = $i.Install()
  $okn = 0; for ($k = 0; $k -lt $col.Count; $k++) {{ if ($res.GetUpdateResult($k).ResultCode -eq 2) {{ $okn++ }} }}
  [pscustomobject]@{{ok=$true; n=$col.Count; okn=$okn; code=$res.ResultCode; reboot=$res.RebootRequired}}
}} catch {{
  [pscustomobject]@{{ ok=$false; code=('0x{{0:X8}}' -f $_.Exception.HResult); err=$_.Exception.Message }}
}}
"""
        r = winutil.ps_run("& {" + script + "} | ConvertTo-Json -Depth 4 -Compress", timeout=3600)
        if r.timed_out:
            return {"ok": False, "msg": "L'installazione sta impiegando troppo. Controlla Windows Update."}
        try:
            d = json.loads(r.out) if r.out else {}
        except ValueError:
            d = {}
        if not isinstance(d, dict) or not d:
            diagnostics.log_error(f"Installazione aggiornamenti: risposta non valida: {r.out[:300]} {r.err[:300]}")
            return {"ok": False, "msg": "Installazione non riuscita: riprova da Impostazioni → Windows Update."}
        if not d.get("ok"):
            diagnostics.log_error(f"Installazione aggiornamenti: {d.get('code')} {d.get('err')}")
            return {"ok": False, "msg": wu_error_text(d.get("code"))}
        n, okn = d.get("n", 0), d.get("okn", d.get("n", 0))
        ok = d.get("code") in (2, 3) and okn > 0 or n == 0
        if n == 0:
            msg = "Questi aggiornamenti sono già stati installati."
        elif okn == n:
            msg = f"Installati {n} aggiornamenti."
        elif okn:
            msg = f"Installati {okn} aggiornamenti su {n}: gli altri riprova più tardi."
        else:
            msg = "Installazione non riuscita: riprova da Impostazioni → Windows Update."
        if d.get("reboot"):
            msg += " Riavvia il PC per completare."
        if okn:
            history.add("update", f"Windows Update: {okn} aggiornamenti installati")
        return {"ok": bool(ok), "msg": msg, "reboot": bool(d.get("reboot"))}

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
            history.add("startup", f"{item['name']}: {'riattivato' if enabled else 'tolto'} dall'avvio automatico",
                        undo={"type": "startup", "name": item["name"], "demo_id": item["id"], "enabled": not enabled})
            return {"ok": True, "msg": f"{item['name']} " + ("riattivato." if enabled else "non partirà più all'avvio.")}
        if item["_aroot"] == winreg.HKEY_LOCAL_MACHINE and not is_admin():
            return {"ok": False, "msg": "Per questo programma servono i permessi di amministratore."}
        try:
            self._write_startup(item["_aroot"], item["_approved"], item["_value"], enabled)
        except PermissionError:
            return {"ok": False, "msg": "Permesso negato: avvia l'app come amministratore."}
        item["enabled"] = enabled
        history.add("startup", f"{item['name']}: {'riattivato' if enabled else 'tolto'} dall'avvio automatico",
                    undo={"type": "startup", "name": item["name"], "value": item["_value"], "approved": item["_approved"],
                          "root": "HKLM" if item["_aroot"] == winreg.HKEY_LOCAL_MACHINE else "HKCU",
                          "enabled": not enabled})
        return {"ok": True, "msg": f"{item['name']} " + ("riattivato." if enabled else "non partirà più all'avvio.")}

    def _write_startup(self, root, approved, value, enabled):
        if enabled:
            data = b"\x02" + b"\x00" * 11
        else:
            filetime = int((datetime.datetime.utcnow() - datetime.datetime(1601, 1, 1)).total_seconds() * 10 ** 7)
            data = b"\x03\x00\x00\x00" + filetime.to_bytes(8, "little")
        with winreg.CreateKeyEx(root, rf"{self._APPROVED}\{approved}", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, value, 0, winreg.REG_BINARY, data)

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
        cmd = (app["_uninstall"] or "").strip()
        if not cmd:
            return {"ok": False, "msg": "Questo programma non dice come disinstallarlo: usa Impostazioni → App."}
        m = re.search(r"\{[0-9A-Fa-f\-]{36}\}", cmd)
        if "msiexec" in cmd.lower() and m:
            # Programmi MSI: si usa sempre "disinstalla" (/X) con il codice del prodotto
            args = [os.path.join(winutil._SYS32, "msiexec.exe"), "/X", m.group(0)]
            started = winutil.start(args, hidden=False)
        else:
            try:
                subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL)
                started = True
            except OSError as e:
                diagnostics.log_error(f"Disinstallazione di {app['name']} non avviata: {e}")
                started = False
        if not started:
            return {"ok": False, "msg": "Impossibile avviare la disinstallazione: prova da Impostazioni → App."}
        history.add("apps", f"Disinstallazione avviata: {app['name']}")
        return {"ok": True, "msg": f"Disinstallazione di {app['name']} avviata: segui la finestra che si è aperta."}

    def open_windows_settings(self, page):
        allowed = {"apps": "ms-settings:appsfeatures", "update": "ms-settings:windowsupdate",
                   "storage": "ms-settings:storagesense", "graphics": "ms-settings:display-advancedgraphics",
                   "protection": "ms-settings:about"}
        if IS_WINDOWS and page in allowed:
            os.startfile(allowed[page])
        return True

    def restart_as_admin(self):
        return relaunch_as_admin()


# --------------------------------------------------------------------------
# Rete di sicurezza: un errore in Python non deve mai bloccare l'interfaccia
# --------------------------------------------------------------------------
# Cosa restituire se una funzione va in errore (l'interfaccia si aspetta questi formati)
_LIST_RESULTS = {"get_drives", "get_gpu_info", "list_startup", "list_apps", "history_list", "running_apps",
                 "find_large_files", "security_threats", "disk_health", "network_ping"}
_BOOL_RESULTS = {"open_url", "open_system_restore", "open_windows_settings", "restart_as_admin", "log_ui_error",
                 "open_winget_store", "open_windows_security"}
_FALLBACKS = {
    "get_prefs": lambda: __import__("updater").load_settings(),
    "set_prefs": lambda: __import__("updater").load_settings(),
    "reset_prefs": lambda: __import__("updater").load_settings(),
    "get_live_stats": lambda: {"cpu_load": 0, "ram_used": 0, "ram_used_gb": 0, "disk_free_gb": 0, "disk_total_gb": 0},
    "scan_junk": lambda: {"categories": [], "items": [], "ok": False},
    "get_gpu_tweaks": lambda: {},
    "get_cpu_info": lambda: {"name": "?"},
    "get_game_mode": lambda: {"state": None, "opts": {}, "apps": []},
    "get_auto_clean": lambda: {"freq": "off", "available": False},
    "network_info": lambda: {"ok": False, "adapters": []},
    "dns_benchmark": lambda: [],
    "speedtest_progress": lambda: {"status": "error", "msg": "Test non riuscito."},
    "driver_progress": lambda: {"status": "error", "msg": "Download non riuscito."},
    "security_progress": lambda: {"status": "idle"},
}
_ERROR_MSG = ("Qualcosa è andato storto. Se succede ancora usa Impostazioni → Aiuto e problemi → "
              "Crea report, così si vede il motivo.")


def _guarded(name, fn):
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            diagnostics.log_error(f"[{name}]\n{traceback.format_exc()}")
            if name in _LIST_RESULTS:
                return []
            if name in _BOOL_RESULTS:
                return False
            if name in _FALLBACKS:
                try:
                    return _FALLBACKS[name]()
                except Exception:
                    pass
            return {"ok": False, "msg": _ERROR_MSG}
    wrapper._pcb_guarded = True
    return wrapper


def harden_api(cls):
    """Avvolge ogni funzione pubblica della classe con la rete di sicurezza."""
    import inspect
    for name, value in list(vars(cls).items()):
        if name.startswith("_") or not inspect.isfunction(value) or getattr(value, "_pcb_guarded", False):
            continue
        setattr(cls, name, _guarded(name, value))
    return cls


harden_api(Api)
