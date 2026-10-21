"""
PC Booster - pulizia del PC (stile CCleaner) e ricerca dei file grandi.

Ogni voce di pulizia ha:
  id, categoria, nome, descrizione, se è selezionata di default,
  le cartelle (anche con * per i profili dei browser) e i processi che la bloccano se aperti.
Si cancella sempre il CONTENUTO delle cartelle di cache, mai la cartella stessa.
I file in uso vengono saltati: è normale e non è un errore.
"""
import os
import sys
import glob
import stat
import time
import shutil
import ctypes
import datetime

import winutil

IS_WINDOWS = sys.platform == "win32"

try:
    import psutil
except ImportError:
    psutil = None


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def _ps(script: str, timeout: int = 600) -> str:
    return winutil.ps(script, timeout)


# --------------------------------------------------------------------------
# Cancellazione sicura
# --------------------------------------------------------------------------
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_REPARSE_POINT = 0x400      # collegamenti, "junction", file di OneDrive
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


def _attrs(st):
    return getattr(st, "st_file_attributes", 0)


def _is_link(st):
    """Vero per collegamenti simbolici e "junction": non si entra mai dentro, si toglie solo il collegamento.
    I file di OneDrive sono anche loro "reparse point", ma non puntano altrove: quelli non contano."""
    if stat.S_ISLNK(st.st_mode):
        return True
    if not _attrs(st) & FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    tag = getattr(st, "st_reparse_tag", 0)
    return not tag or bool(tag & 0x20000000)    # 0x20000000 = punta a un altro percorso


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def _protected_roots():
    """Cartelle che non si cancellano mai, nemmeno se una variabile di Windows fosse sbagliata."""
    env = os.environ
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    sysdrive = (env.get("SystemDrive") or "C:") + os.sep
    roots = {sysdrive, home, os.path.dirname(home),
             env.get("WINDIR", ""), os.path.join(env.get("WINDIR", ""), "System32"),
             env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", ""), env.get("PROGRAMDATA", ""),
             env.get("APPDATA", ""), env.get("LOCALAPPDATA", ""), os.path.join(home, "AppData"),
             os.path.join(home, "AppData", "LocalLow"), os.path.join(home, "Documents"),
             os.path.join(home, "Desktop"), os.path.join(home, "Downloads"), os.path.join(home, "Pictures"),
             os.path.join(home, "Videos"), os.path.join(home, "Music"), "/", "/home", "/root", "/tmp"}
    return {_norm(r) for r in roots if r}


def _safe_dir(path):
    """Una cartella da svuotare deve essere un percorso completo, abbastanza "profondo" e non protetto."""
    if not path or not os.path.isabs(path):
        return False
    n = _norm(path)
    parts = [x for x in n.replace("\\", "/").split("/") if x]
    return len(parts) >= 3 and n not in _protected_roots()


def _own_dirs():
    """Cartelle dell'app in uso: PyInstaller si estrae in %TEMP%\\_MEIxxxx mentre l'app è aperta."""
    out = set()
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        out.add(_norm(mei))
    return out


def _young(st, min_age, now):
    born = getattr(st, "st_birthtime", None) or getattr(st, "st_ctime", 0)
    return bool(min_age) and now - max(st.st_mtime, born) < min_age


def _skip_entry(name, path, st, min_age, now):
    """Voci da lasciare stare: l'app stessa (e le altre app PyInstaller aperte) e i file appena creati."""
    low = name.lower()
    if low.startswith("_mei") or low.startswith("pcbooster") or _norm(path) in _own_dirs():
        return True
    return _young(st, min_age, now)


def _make_writable(path, st):
    if _attrs(st) & FILE_ATTRIBUTE_READONLY or not (st.st_mode & stat.S_IWRITE):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def _remove(path, st=None, min_age=0, now=0):
    """Elimina un file o una cartella senza mai seguire i collegamenti.
    Restituisce (byte liberati, file in uso saltati, file recenti lasciati)."""
    try:
        st = st or os.lstat(path)
    except OSError:
        return 0, 0, 0
    if _is_link(st):
        try:
            if stat.S_ISDIR(st.st_mode) or (_attrs(st) & 0x10):   # 0x10 = cartella
                os.rmdir(path)            # toglie solo il collegamento, non quello a cui punta
            else:
                os.unlink(path)
        except OSError:
            return 0, 1, 0
        return 0, 0, 0
    if stat.S_ISDIR(st.st_mode):
        freed = skipped = kept = 0
        try:
            entries = list(os.scandir(path))
        except OSError:
            return 0, 1, 0
        for e in entries:
            f, sk, kp = _remove(e.path, None, min_age, now)
            freed += f
            skipped += sk
            kept += kp
        if not skipped and not kept:
            try:
                os.rmdir(path)
            except OSError:
                pass
        return freed, skipped, kept
    if _young(st, min_age, now):
        return 0, 0, 1
    _make_writable(path, st)
    try:
        os.remove(path)
        return st.st_size, 0, 0
    except OSError:
        return 0, 1, 0


def _size(path: str) -> int:
    """Spazio occupato, senza entrare nei collegamenti."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if _is_link(st):
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    est = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link(est):
                    continue
                if stat.S_ISDIR(est.st_mode):
                    stack.append(e.path)
                else:
                    total += est.st_size
    return total


def _running():
    if psutil is None:
        return set()
    names = set()
    for p in psutil.process_iter(["name"]):
        try:
            names.add((p.info["name"] or "").lower())
        except Exception:
            pass
    return names


# --------------------------------------------------------------------------
# Cosa si può pulire
# --------------------------------------------------------------------------
CATEGORIES = [
    ("windows", "Windows"),
    ("browser", "Browser"),
    ("games", "Giochi e scheda video"),
    ("apps", "App"),
    ("deep", "Pulizia profonda"),
]

CHROMIUM_CACHE = ["Cache", "Code Cache", "GPUCache", r"Service Worker\CacheStorage", r"Service Worker\ScriptCache"]


def _targets():
    env = os.environ
    local = env.get("LOCALAPPDATA", "")
    roaming = env.get("APPDATA", "")
    locallow = os.path.join(env.get("USERPROFILE", ""), "AppData", "LocalLow")
    windir = env.get("WINDIR", r"C:\Windows")
    progdata = env.get("PROGRAMDATA", r"C:\ProgramData")
    pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    sysdrive = env.get("SystemDrive", "C:") + "\\"

    t = [
        # ---- Windows ----
        dict(id="user_temp", cat="windows", label="File temporanei dell'utente", default=True,
             desc="File lasciati dai programmi mentre lavorano. Quelli di oggi restano (potrebbero servire ancora).",
             dirs=[env.get("TEMP", "")], min_age=24 * 3600),
        dict(id="win_temp", cat="windows", label="File temporanei di Windows", default=True,
             desc="Temporanei di sistema.", dirs=[os.path.join(windir, "Temp")], min_age=24 * 3600),
        dict(id="wu_cache", cat="windows", label="Download di Windows Update", default=True,
             desc="Aggiornamenti già installati: non servono più.",
             dirs=[os.path.join(windir, "SoftwareDistribution", "Download")], special="wu"),
        dict(id="delivery", cat="windows", label="Cache di ottimizzazione recapito", default=True,
             desc="Pezzi di aggiornamenti condivisi con altri PC.",
             dirs=[os.path.join(windir, r"ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Cache")],
             special="delivery"),
        dict(id="crash", cat="windows", label="Report degli errori", default=True,
             desc="Rapporti di crash già inviati o vecchi.",
             dirs=[os.path.join(local, "CrashDumps"), os.path.join(progdata, r"Microsoft\Windows\WER\ReportArchive"),
                   os.path.join(progdata, r"Microsoft\Windows\WER\ReportQueue")]),
        dict(id="dumps", cat="windows", label="Dump della memoria", default=True,
             desc="File creati dalle schermate blu. Possono essere molto grandi.",
             dirs=[os.path.join(windir, "Minidump")], files=[os.path.join(windir, "MEMORY.DMP"),
                                                            os.path.join(windir, "LiveKernelReports", "*.dmp")]),
        dict(id="logs", cat="windows", label="Log di Windows", default=True,
             desc="Registri di installazione e manutenzione.",
             files=[os.path.join(windir, r"Logs\CBS\*.log"), os.path.join(windir, r"Logs\CBS\*.cab"),
                    os.path.join(windir, r"Logs\DISM\*.log"), os.path.join(windir, r"Logs\WindowsUpdate\*.etl")]),
        dict(id="thumbs", cat="windows", label="Cache miniature e icone", default=False,
             desc="Windows le ricrea: le cartelle con tante foto si aprono un po' più lente la prima volta.",
             files=[os.path.join(local, r"Microsoft\Windows\Explorer\thumbcache_*.db"),
                    os.path.join(local, r"Microsoft\Windows\Explorer\iconcache_*.db")]),
        dict(id="recycle", cat="windows", label="Cestino", default=False,
             desc="I file nel cestino vengono eliminati per sempre.", special="recycle"),
        dict(id="dns", cat="windows", label="Cache DNS", default=True,
             desc="Aiuta quando un sito non si apre. Non libera spazio.", special="dns"),

        # ---- Giochi e scheda video ----
        dict(id="dx_shader", cat="games", label="Cache shader di DirectX", default=False,
             desc="Utile se i giochi scattano dopo un aggiornamento dei driver. Il primo avvio dei giochi sarà più lento.",
             dirs=[os.path.join(local, "D3DSCache")]),
        dict(id="nv_cache", cat="games", label="Cache shader NVIDIA", default=False,
             desc="Come sopra, per le schede NVIDIA.",
             dirs=[os.path.join(local, r"NVIDIA\DXCache"), os.path.join(local, r"NVIDIA\GLCache"),
                   os.path.join(locallow, r"NVIDIA\PerDriverVersion\DXCache"),
                   os.path.join(locallow, r"NVIDIA\PerDriverVersion\GLCache"),
                   os.path.join(progdata, r"NVIDIA Corporation\NV_Cache")]),
        dict(id="amd_cache", cat="games", label="Cache shader AMD", default=False,
             desc="Come sopra, per le schede AMD.",
             dirs=[os.path.join(local, r"AMD\DxCache"), os.path.join(local, r"AMD\DxcCache"),
                   os.path.join(local, r"AMD\GLCache"), os.path.join(local, r"AMD\VkCache")]),
        dict(id="intel_cache", cat="games", label="Cache shader Intel", default=False,
             desc="Come sopra, per la grafica Intel.", dirs=[os.path.join(locallow, r"Intel\ShaderCache")]),
        dict(id="steam", cat="games", label="Steam: cache web e log", default=True,
             desc="Pagine del negozio e registri. Non tocca giochi e salvataggi.", procs=["steam.exe"],
             dirs=[os.path.join(local, r"Steam\htmlcache"), os.path.join(pf86, r"Steam\logs"),
                   os.path.join(pf86, r"Steam\dumps")]),
        dict(id="epic", cat="games", label="Epic Games: cache web e log", default=True,
             desc="Non tocca giochi e salvataggi.", procs=["epicgameslauncher.exe"],
             dirs=[os.path.join(local, r"EpicGamesLauncher\Saved\webcache*"),
                   os.path.join(local, r"EpicGamesLauncher\Saved\Logs")]),

        # ---- App ----
        dict(id="discord", cat="apps", label="Discord", default=True, desc="Immagini e video già visti.",
             procs=["discord.exe"],
             dirs=[os.path.join(roaming, r"discord\Cache"), os.path.join(roaming, r"discord\Code Cache"),
                   os.path.join(roaming, r"discord\GPUCache")]),
        dict(id="spotify", cat="apps", label="Spotify", default=True,
             desc="Brani salvati per l'ascolto veloce (non quelli scaricati offline).", procs=["spotify.exe"],
             dirs=[os.path.join(local, r"Spotify\Data")]),

        # ---- Pulizia profonda ----
        dict(id="dism", cat="deep", label="Componenti vecchi di Windows", default=False,
             desc="Elimina le vecchie versioni dei file di sistema lasciate dagli aggiornamenti. "
                  "Può richiedere 10-20 minuti.", special="dism"),
        dict(id="hibernate", cat="deep", label="File di ibernazione (hiberfil.sys)", default=False,
             desc="Spegne l'ibernazione e l'avvio rapido di Windows: libera tanto spazio quanta RAM hai. "
                  "Sconsigliato sui portatili che usi a batteria. Si può riattivare dalla Cronologia.",
             special="hibernate"),
        dict(id="winold", cat="deep", label="Vecchia installazione di Windows (Windows.old)", default=False,
             desc="Dopo averla eliminata non potrai più tornare alla versione precedente di Windows.",
             dirs=[os.path.join(sysdrive, "Windows.old")], special="winold"),
    ]

    # ---- Browser (solo cache: password, cronologia e login restano) ----
    chromium = [
        ("chrome", "Google Chrome", os.path.join(local, r"Google\Chrome\User Data"), ["chrome.exe"], True),
        ("edge", "Microsoft Edge", os.path.join(local, r"Microsoft\Edge\User Data"), ["msedge.exe"], True),
        ("brave", "Brave", os.path.join(local, r"BraveSoftware\Brave-Browser\User Data"), ["brave.exe"], True),
        ("vivaldi", "Vivaldi", os.path.join(local, r"Vivaldi\User Data"), ["vivaldi.exe"], True),
        ("opera", "Opera", os.path.join(local, r"Opera Software\Opera Stable"), ["opera.exe"], False),
        ("operagx", "Opera GX", os.path.join(local, r"Opera Software\Opera GX Stable"), ["opera.exe"], False),
    ]
    for bid, name, base, procs, multi in chromium:
        if not os.path.isdir(base):
            continue
        profiles = [os.path.join(base, "Default"), os.path.join(base, "Profile *")] if multi else [base]
        dirs = [os.path.join(p, c) for p in profiles for c in CHROMIUM_CACHE]
        t.append(dict(id=f"br_{bid}", cat="browser", label=name, default=True, procs=procs, dirs=dirs,
                      desc="Cache delle pagine. Password, preferiti e accessi ai siti restano."))
    gecko = [
        ("firefox", "Mozilla Firefox", os.path.join(local, r"Mozilla\Firefox\Profiles"), ["firefox.exe"]),
        ("zen", "Zen Browser", os.path.join(local, r"zen\Profiles"), ["zen.exe"]),
    ]
    for bid, name, base, procs in gecko:
        if not os.path.isdir(base):
            continue
        dirs = [os.path.join(base, "*", c) for c in ("cache2", "startupCache", "thumbnails")]
        t.append(dict(id=f"br_{bid}", cat="browser", label=name, default=True, procs=procs, dirs=dirs,
                      desc="Cache delle pagine. Password, preferiti e accessi ai siti restano."))
    return t


def _hiberfil_path():
    return os.path.join((os.environ.get("SystemDrive") or "C:") + "\\", "hiberfil.sys")


def _winold_path():
    return os.path.join((os.environ.get("SystemDrive") or "C:") + "\\", "Windows.old")


def _expand(patterns):
    """Percorsi reali (con * espanso). I percorsi non completi o protetti vengono scartati."""
    out = []
    for p in patterns or []:
        if not p or not os.path.isabs(p):
            continue
        for x in (glob.glob(p) if any(c in p for c in "*?") else [p]):
            if _safe_dir(x) and x not in out:
                out.append(x)
    return out


# --------------------------------------------------------------------------
# Dati di esempio (fuori da Windows, solo per provare l'interfaccia)
# --------------------------------------------------------------------------
DEMO_SIZES = {"user_temp": 2_300_000_000, "win_temp": 310_000_000, "wu_cache": 3_900_000_000,
              "delivery": 1_200_000_000, "crash": 95_000_000, "dumps": 820_000_000, "logs": 260_000_000,
              "thumbs": 180_000_000, "recycle": 640_000_000, "dns": 0, "dx_shader": 1_100_000_000,
              "nv_cache": 900_000_000, "steam": 450_000_000, "epic": 120_000_000, "discord": 380_000_000,
              "spotify": 1_500_000_000, "br_chrome": 690_000_000, "br_zen": 240_000_000, "br_edge": 310_000_000,
              "dism": None, "winold": 0, "hibernate": 25_600_000_000}
DEMO_ITEMS = [("user_temp", "windows"), ("win_temp", "windows"), ("wu_cache", "windows"), ("delivery", "windows"),
              ("crash", "windows"), ("dumps", "windows"), ("logs", "windows"), ("thumbs", "windows"),
              ("recycle", "windows"), ("dns", "windows"), ("br_zen", "browser"), ("br_chrome", "browser"),
              ("br_edge", "browser"), ("dx_shader", "games"), ("nv_cache", "games"), ("steam", "games"),
              ("epic", "games"), ("discord", "apps"), ("spotify", "apps"), ("dism", "deep"), ("hibernate", "deep"), ("winold", "deep")]


def _send_to_recycle_bin(path):
    """Sposta un file nel Cestino con la funzione di Windows (senza finestre di conferma)."""
    if not IS_WINDOWS:
        return False
    try:
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE = 3
        FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
    except Exception as e:
        winutil._log(f"Cestino: {e}")
        return False


class Cleaner:
    def __init__(self):
        self._targets = {}
        self._large = []

    def scan(self):
        if not IS_WINDOWS:
            return self._demo_scan()
        running = _running()
        items = []
        for t in _targets():
            self._targets[t["id"]] = t
            size = None
            sp = t.get("special")
            if sp == "recycle":
                out = _ps("$s=0; (New-Object -ComObject Shell.Application).NameSpace(10).Items() | "
                          "ForEach-Object { $s += $_.Size }; $s", 60)
                try:
                    size = int(float(out or 0))
                except ValueError:
                    size = 0
            elif sp == "dns":
                size = 0
            elif sp == "dism":
                size = None  # non calcolabile in anticipo
            elif sp == "winold":
                path = _winold_path()
                size = _size(path) if os.path.isdir(path) else 0
            elif sp == "hibernate":
                try:
                    size = os.path.getsize(_hiberfil_path())
                except OSError:
                    size = 0
            else:
                size = sum(_size(p) for p in _expand(t.get("dirs")) if os.path.isdir(p))
                size += sum(_size(f) for f in _expand(t.get("files")) if os.path.isfile(f))
            if sp in ("winold", "hibernate") and not size:
                continue  # niente Windows.old o ibernazione già spenta: non mostrare la voce
            busy = [p for p in t.get("procs", []) if p in running]
            items.append(self._row(t, size, bool(busy)))
        return {"categories": [{"id": c, "label": l} for c, l in CATEGORIES], "items": items}

    def _demo_scan(self):
        all_t = {t["id"]: t for t in _demo_targets()}
        items = []
        for tid, _cat in DEMO_ITEMS:
            t = all_t[tid]
            self._targets[tid] = t
            items.append(self._row(t, DEMO_SIZES.get(tid), tid in ("br_zen", "discord")))
        return {"categories": [{"id": c, "label": l} for c, l in CATEGORIES], "items": items}

    @staticmethod
    def _row(t, size, busy):
        return {"id": t["id"], "cat": t["cat"], "label": t["label"], "desc": t["desc"],
                "default": t["default"], "bytes": size, "size": "—" if size is None else human_size(size),
                "busy": busy, "special": t.get("special")}

    # ---------- pulizia ----------
    def clean(self, ids):
        if not IS_WINDOWS:
            freed = sum(DEMO_SIZES.get(i) or 0 for i in ids)
            return {"ok": True, "freed": human_size(freed), "bytes": freed,
                    "msg": f"Modalità demo: avrei liberato {human_size(freed)}.", "hibernate_off": "hibernate" in ids}
        if not self._targets:
            self.scan()
        freed, skipped, notes = 0, 0, []
        hibernate_off = False
        for tid in ids:
            t = self._targets.get(tid)
            if not t:
                continue
            sp = t.get("special")
            if sp == "recycle":
                before = self._recycle_size()
                _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
                freed += before
                continue
            if sp == "dns":
                winutil.run(["ipconfig", "/flushdns"], timeout=20)
                continue
            if sp == "delivery":
                before = sum(_size(p) for p in _expand(t.get("dirs")))
                _ps("Delete-DeliveryOptimizationCache -Force -ErrorAction SilentlyContinue")
                freed += max(0, before - sum(_size(p) for p in _expand(t.get("dirs"))))
                continue
            if sp == "dism":
                drive = os.environ.get("SystemDrive", "C:") + "\\"
                before = shutil.disk_usage(drive).free
                # DISM non va mai interrotto a metà: se ci mette più di un'ora lo si lascia finire da solo
                r = winutil.run(["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=3600,
                                stop_on_timeout=False)
                freed += max(0, shutil.disk_usage(drive).free - before)
                if r.ok:
                    notes.append("Componenti vecchi di Windows ripuliti.")
                elif r.timed_out:
                    notes.append("La pulizia dei componenti di Windows continua in background: finirà da sola.")
                else:
                    notes.append("La pulizia dei componenti di Windows non è riuscita (riprova dopo un riavvio).")
                continue
            if sp == "hibernate":
                before = 0
                try:
                    before = os.path.getsize(_hiberfil_path())
                except OSError:
                    pass
                if winutil.run(["powercfg", "/h", "off"], timeout=60).ok:
                    hibernate_off = True
                    freed += before
                continue
            if sp == "winold":
                path = _winold_path()
                if not os.path.isdir(path) or os.path.basename(path).lower() != "windows.old":
                    continue
                before = _size(path)
                for cmd in (["takeown", "/F", path, "/R", "/A", "/D", "Y"],
                            ["icacls", path, "/grant", "*S-1-5-32-544:F", "/T", "/C", "/Q"],
                            ["cmd", "/c", "rmdir", "/s", "/q", path]):
                    winutil.run(cmd, timeout=1800)
                freed += before if not os.path.exists(path) else max(0, before - _size(path))
                continue
            if sp == "wu":
                _ps("Stop-Service wuauserv,bits -Force -ErrorAction SilentlyContinue")
            f, s = self._wipe(t)
            freed += f
            skipped += s
            if sp == "wu":
                _ps("Start-Service wuauserv,bits -ErrorAction SilentlyContinue")
        msg = f"Liberati {human_size(freed)}."
        if skipped:
            msg += f" {skipped} file erano in uso e sono stati lasciati."
        if notes:
            msg += " " + " ".join(notes)
        return {"ok": True, "freed": human_size(freed), "bytes": freed, "msg": msg, "hibernate_off": hibernate_off}

    @staticmethod
    def _wipe(t):
        """Svuota le cartelle della voce (mai la cartella stessa) e cancella i file indicati."""
        freed = skipped = 0
        min_age = t.get("min_age", 0)
        now = time.time()
        for d in _expand(t.get("dirs")):
            try:
                dst = os.lstat(d)
            except OSError:
                continue
            if _is_link(dst) or not stat.S_ISDIR(dst.st_mode):
                continue    # la cartella di cache è un collegamento verso altro: non si tocca
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _skip_entry(e.name, e.path, st, min_age, now):
                    continue
                f, sk, _kept = _remove(e.path, st, min_age, now)
                freed += f
                skipped += sk
        for f in _expand(t.get("files")):
            try:
                st = os.lstat(f)
            except OSError:
                continue
            if _is_link(st) or stat.S_ISDIR(st.st_mode):
                continue
            fr, sk, _kept = _remove(f, st)
            freed += fr
            skipped += sk
        return freed, skipped

    @staticmethod
    def _recycle_size():
        out = _ps("$s=0; (New-Object -ComObject Shell.Application).NameSpace(10).Items() | "
                  "ForEach-Object { $s += $_.Size }; $s", 60)
        try:
            return int(float(out or 0))
        except ValueError:
            return 0

    # ---------- file grandi ----------
    def find_large_files(self, min_mb=250, limit=60):
        if not IS_WINDOWS:
            now = datetime.datetime.now()
            demo = [("Registrazione partita.mp4", r"C:\Users\Demo\Videos\Captures", 8_600_000_000, 40),
                    ("Windows11_24H2.iso", r"C:\Users\Demo\Downloads", 5_800_000_000, 210),
                    ("backup_telefono.zip", r"C:\Users\Demo\Documents", 3_100_000_000, 400),
                    ("GTA_mod_pack.rar", r"C:\Users\Demo\Downloads", 1_900_000_000, 95),
                    ("setup_vecchio_gioco.exe", r"C:\Users\Demo\Downloads", 740_000_000, 600),
                    ("Vacanze 2025.mov", r"C:\Users\Demo\Pictures", 520_000_000, 300)]
            self._large = [{"id": str(i), "name": n, "folder": f, "path": os.path.join(f, n), "bytes": b,
                            "size": human_size(b), "date": (now - datetime.timedelta(days=d)).strftime("%Y-%m-%d"),
                            "days": d} for i, (n, f, b, d) in enumerate(demo)]
            return self._large
        home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
        roots = [os.path.join(home, d) for d in ("Desktop", "Downloads", "Documents", "Videos", "Pictures", "Music")]
        roots += glob.glob(os.path.join(home, "OneDrive*"))
        min_bytes = min_mb * 1024 * 1024
        found = []
        seen = set()
        for root in roots:
            stack = [root] if os.path.isdir(root) else []
            while stack:
                d = stack.pop()
                try:
                    it = os.scandir(d)
                except OSError:
                    continue
                with it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if _is_link(st):
                            continue   # collegamenti e "junction": si salta (niente doppioni né giri infiniti)
                        if stat.S_ISDIR(st.st_mode):
                            if not e.name.startswith(".") and e.name.lower() != "appdata":
                                stack.append(e.path)
                            continue
                        if _attrs(st) & CLOUD_ONLY:
                            continue   # file solo su OneDrive: non occupa spazio sul PC
                        if st.st_size >= min_bytes and e.path.lower() not in seen:
                            seen.add(e.path.lower())
                            found.append((e.path, st.st_size, st.st_mtime))
        found.sort(key=lambda x: x[1], reverse=True)
        now = datetime.datetime.now()
        self._large = []
        for i, (p, size, mtime) in enumerate(found[:limit]):
            d = datetime.datetime.fromtimestamp(mtime)
            self._large.append({"id": str(i), "name": os.path.basename(p), "folder": os.path.dirname(p), "path": p,
                                "bytes": size, "size": human_size(size), "date": d.strftime("%Y-%m-%d"),
                                "days": (now - d).days})
        return self._large

    def _large_item(self, item_id):
        return next((x for x in self._large if x["id"] == item_id), None)

    def reveal_file(self, item_id):
        it = self._large_item(item_id)
        if it and IS_WINDOWS:
            winutil.start(["explorer", "/select,", it["path"]], hidden=False)
        return {"ok": bool(it)}

    def recycle_file(self, item_id):
        """Sposta il file nel Cestino (si può recuperare)."""
        it = self._large_item(item_id)
        if not it:
            return {"ok": False, "msg": "File non trovato, aggiorna l'elenco."}
        if not IS_WINDOWS:
            return {"ok": True, "msg": f"Demo: {it['name']} spostato nel Cestino."}
        if not os.path.isfile(it["path"]):
            return {"ok": False, "msg": "Il file non c'è più: aggiorna l'elenco."}
        if not _send_to_recycle_bin(it["path"]):
            safe = winutil.q(it["path"])
            _ps("Add-Type -AssemblyName Microsoft.VisualBasic;"
                f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{safe}','OnlyErrorDialogs','SendToRecycleBin')", 120)
        if os.path.exists(it["path"]):
            return {"ok": False, "msg": "Non riesco a spostarlo: forse è aperto in un altro programma."}
        return {"ok": True, "msg": f"{it['name']} spostato nel Cestino."}


def _demo_targets():
    """Le stesse voci di Windows, con cartelle finte, per la modalità demo."""
    base = {t["id"]: t for t in _targets_for_demo()}
    return list(base.values())


def _targets_for_demo():
    saved = dict(os.environ)
    try:
        for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "WINDIR", "PROGRAMDATA", "TEMP"):
            os.environ.setdefault(k, "/nonexistent")
        items = _targets()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    extra = [dict(id="br_zen", cat="browser", label="Zen Browser", default=True, procs=["zen.exe"],
                  desc="Cache delle pagine. Password, preferiti e accessi ai siti restano."),
             dict(id="br_chrome", cat="browser", label="Google Chrome", default=True, procs=["chrome.exe"],
                  desc="Cache delle pagine. Password, preferiti e accessi ai siti restano."),
             dict(id="br_edge", cat="browser", label="Microsoft Edge", default=True, procs=["msedge.exe"],
                  desc="Cache delle pagine. Password, preferiti e accessi ai siti restano.")]
    return items + extra
