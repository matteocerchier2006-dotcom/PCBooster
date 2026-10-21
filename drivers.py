"""
PC Booster - ultima versione dei driver della scheda video (NVIDIA e AMD).

NVIDIA: servizio ufficiale usato anche dal sito nvidia.com
  1) elenco prodotti  -> numero del modello (pfid)
  2) ricerca driver   -> ultima versione Game Ready, data e link di download
AMD: AMD non ha un servizio pubblico. Si usa il progetto open source
  github.com/nunodxxd/AMD-Software-Adrenalin, che ogni giorno legge il sito AMD
  e pubblica l'ultima versione stabile con il link ufficiale drivers.amd.com.
Il download avviene in background; poi si apre l'installazione ufficiale del produttore.
"""
import os
import re
import json
import time
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import winutil

IS_WINDOWS = os.name == "nt"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PCBooster"

NV_PRODUCTS = "https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=3"
NV_LOOKUP = ("https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
             "?func=DriverManualLookup&pfid={pfid}&osID={os}&dch={dch}")
NV_GPU_DATA = "https://raw.githubusercontent.com/ZenitH-AT/nvidia-data/main/gpu-data.json"
AMD_LATEST = "https://api.github.com/repos/nunodxxd/AMD-Software-Adrenalin/releases/latest"


def _get(url, headers=None, timeout=20):
    h = {"User-Agent": UA}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read()


def vparse(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


# ---------------- NVIDIA ----------------
_nv_products = None


def _nvidia_pfid(gpu_name):
    global _nv_products
    if not _nv_products:
        products = {}
        try:   # elenco ufficiale NVIDIA
            root = ET.fromstring(_get(NV_PRODUCTS))
            for lv in root.iter("LookupValue"):
                name, value = lv.findtext("Name"), lv.findtext("Value")
                if name and value:
                    products.setdefault(name.strip().lower(), value.strip())
        except Exception:
            pass
        if not products:   # copia dello stesso elenco mantenuta dal progetto nvidia-update
            data = json.loads(_get(NV_GPU_DATA).decode("utf-8"))
            for group in data.values():
                if isinstance(group, dict):
                    for name, value in group.items():
                        products.setdefault(str(name).strip().lower(), str(value))
        _nv_products = products   # si tiene solo se è andato a buon fine (altrimenti si riprova la prossima volta)
    name = re.sub(r"^nvidia\s+", "", gpu_name.strip(), flags=re.I).lower()
    for candidate in (name, name.replace(" laptop gpu", " laptop"), name + " laptop gpu"):
        if candidate in _nv_products:
            return _nv_products[candidate]
    return None


def nvidia_latest(gpu_name):
    pfid = _nvidia_pfid(gpu_name)
    if not pfid:
        return {"ok": False, "msg": "Non trovo questa scheda nell'elenco NVIDIA."}
    info = None
    # Windows 10/11 64 bit (57) e Windows 11 (135), driver DCH e standard
    for os_id, dch in (("57", "1"), ("135", "1"), ("57", "0")):
        try:
            data = json.loads(_get(NV_LOOKUP.format(pfid=pfid, os=os_id, dch=dch)).decode("utf-8", "replace"))
        except Exception:
            continue
        ids = data.get("IDS") or []
        cand = ids[0].get("downloadInfo", {}) if ids else {}
        if str(data.get("Success")) == "1" and cand.get("Version"):
            info = cand
            break
    if not info:
        return {"ok": False, "msg": "NVIDIA non mi dà l'ultima versione: controlla dall'app NVIDIA o dal sito."}
    return {"ok": True, "version": info.get("Version", ""), "url": info.get("DownloadURL", ""),
            "date": urllib.parse.unquote(info.get("ReleaseDateTime", "")),
            "name": urllib.parse.unquote(info.get("Name", "")).replace("+", " "),
            "size": urllib.parse.unquote(info.get("DownloadURLFileSize", "")),
            "details": info.get("DetailsURL", "")}


# ---------------- AMD ----------------
def amd_latest():
    rel = json.loads(_get(AMD_LATEST, {"Accept": "application/vnd.github+json"}).decode("utf-8"))
    body = rel.get("body") or ""
    links = re.findall(r"https://drivers\.amd\.com/drivers/[^\s)\"'\]]+\.exe", body)
    version = ".".join(str(x) for x in vparse(rel.get("tag_name", ""))[:3])
    if not links and not version:
        return {"ok": False, "msg": "Non riesco a leggere l'ultima versione AMD."}
    link = next((l for l in links if version in l), links[0] if links else "")
    return {"ok": True, "version": version, "url": link, "date": (rel.get("published_at") or "")[:10],
            "name": "AMD Software: Adrenalin Edition", "size": "",
            "details": f"https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-WIN-{version.replace('.', '-')}.html"}


def check_latest(gpu):
    """gpu: dict di backend.get_gpu_info(). Confronta la versione installata con l'ultima."""
    vendor = gpu.get("vendor")
    try:
        if vendor == "nvidia":
            r = nvidia_latest(gpu.get("name", ""))
            installed = gpu.get("driver_version", "")
        elif vendor == "amd":
            r = amd_latest()
            installed = gpu.get("driver_version", "").replace("Adrenalin", "").strip()
        else:
            return {"ok": False, "msg": "Il controllo automatico c'è per NVIDIA e AMD."}
    except Exception as e:
        return {"ok": False, "msg": f"Non riesco a controllare online ({e.__class__.__name__})."}
    if not r.get("ok"):
        return r
    r["installed"] = installed
    known = bool(vparse(installed))
    r["update"] = known and vparse(r["version"]) > vparse(installed)
    r["unknown_installed"] = not known
    return r


# ---------------- download e installazione ----------------
ALLOWED_DOWNLOAD = ("https://us.download.nvidia.com/", "https://international.download.nvidia.com/",
                    "https://uk.download.nvidia.com/", "https://download.nvidia.com/", "https://drivers.amd.com/")
SIGNERS = {"nvidia": "NVIDIA Corporation", "amd": "Advanced Micro Devices"}


def _host_ok(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return url.lower().startswith("https://") and (host.endswith(".nvidia.com") or host.endswith(".amd.com")
                                                   or host in ("nvidia.com", "amd.com"))


def check_signature(path, vendor):
    """Controlla la firma digitale del file: deve essere valida e del produttore giusto.
    Così si avvia solo un'installazione originale NVIDIA o AMD, mai un file cambiato."""
    if not IS_WINDOWS:
        return True, ""
    out = winutil.ps(f"$s=Get-AuthenticodeSignature -LiteralPath '{winutil.q(path)}';"
                     "\"$($s.Status)|$($s.SignerCertificate.Subject)\"", 120)
    status, _, subject = out.partition("|")
    want = SIGNERS.get(vendor, "")
    if status.strip() != "Valid":
        return False, f"firma non valida ({status.strip() or 'nessuna'})"
    if want and want.lower() not in subject.lower():
        return False, f"firmato da un altro ({subject[:80]})"
    return True, subject


class DriverDownloader:
    def __init__(self):
        self.state = {"status": "idle"}
        self._lock = threading.Lock()

    def start(self, url, vendor):
        if not url or not url.startswith(ALLOWED_DOWNLOAD) or vendor not in SIGNERS:
            return {"ok": False, "msg": "Indirizzo di download non valido."}
        with self._lock:
            if self.state.get("status") in ("downloading", "installing", "launched"):
                return {"ok": False, "msg": "Un download è già in corso."}
            self.state = {"status": "downloading", "done": 0, "total": 0, "vendor": vendor}
        threading.Thread(target=self._run, args=(url, vendor), daemon=True).start()
        return {"ok": True}

    @staticmethod
    def _folder():
        folder = os.path.join(os.environ.get("TEMP") or os.path.expanduser("~"), "PCBooster", "driver")
        os.makedirs(folder, exist_ok=True)
        # i driver scaricati più di una settimana fa non servono più (sono centinaia di MB)
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            try:
                if os.path.isfile(p) and time.time() - os.path.getmtime(p) > 7 * 86400:
                    os.remove(p)
            except OSError:
                pass
        return folder

    def _run(self, url, vendor):
        try:
            self._download_and_launch(url, vendor)
        except Exception as e:
            winutil._log(f"Driver {vendor}: {e.__class__.__name__}: {e}")
            try:   # niente file lasciati a metà
                folder = self._folder()
                for f in os.listdir(folder):
                    if f.endswith(".part"):
                        os.remove(os.path.join(folder, f))
            except OSError:
                pass
            self.state = {"status": "error", "msg": f"Download non riuscito ({e.__class__.__name__}). "
                                                    "Riprova o scaricalo dal sito del produttore."}

    def _download_and_launch(self, url, vendor):
        folder = self._folder()
        name = re.sub(r"[^\w.\-]", "_", urllib.parse.unquote(url.rsplit("/", 1)[-1].split("?")[0]))[:120]
        if not name.lower().endswith(".exe"):
            name += ".exe"
        path = os.path.join(folder, name)
        part = path + ".part"
        headers = {"User-Agent": UA}
        if vendor == "amd":
            headers["Referer"] = "https://www.amd.com/"   # AMD lo richiede per i download
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            if not _host_ok(r.geturl()):
                raise IOError(f"reindirizzato a un sito non ufficiale: {r.geturl()}")
            total = int(r.headers.get("Content-Length") or 0)
            self.state["total"] = total
            while chunk := r.read(1024 * 512):
                f.write(chunk)
                self.state["done"] += len(chunk)
        size = os.path.getsize(part)
        if size < 5_000_000 or (total and size != total):
            os.remove(part)
            raise IOError(f"file incompleto ({size} di {total} byte)")
        os.replace(part, path)
        self.state = {"status": "installing", "path": path, "msg": "Controllo la firma del produttore…"}
        ok, why = check_signature(path, vendor)
        if not ok:
            winutil._log(f"Driver {vendor} scartato: {why}")
            try:
                os.remove(path)
            except OSError:
                pass
            self.state = {"status": "error", "msg": "Il file scaricato non ha la firma originale del produttore: "
                                                    "per sicurezza non l'ho aperto. Scaricalo dal sito ufficiale."}
            return
        proc = None
        if IS_WINDOWS:
            try:
                import subprocess
                proc = subprocess.Popen([path], cwd=folder, stdin=subprocess.DEVNULL, close_fds=True)
            except OSError as e:
                winutil._log(f"Installazione driver non avviata: {e}")
                self.state = {"status": "error", "msg": "Non riesco ad avviare l'installazione del driver."}
                return
        self.state = {"status": "launched", "vendor": vendor,
                      "msg": "Installazione in corso: segui la finestra del produttore. "
                             "Quando finisce, questa pagina si aggiorna da sola."}
        self._wait_installer(proc, folder, vendor)

    def _wait_installer(self, proc, folder, vendor):
        """Aspetta che l'installazione finisca davvero: il file scaricato di solito si estrae e avvia
        un altro programma (setup.exe) che continua dopo. Si seguono tutti, poi si dice "finito"."""
        started = time.time()
        if proc is None:                       # modalità demo
            time.sleep(3)
            self.state = {"status": "finished", "vendor": vendor, "msg": "Installazione finita."}
            return
        try:
            import psutil
        except ImportError:
            psutil = None
        roots = [os.path.normcase(folder)]
        drive = os.environ.get("SystemDrive", "C:")
        roots += [os.path.normcase(os.path.join(drive + "\\", d)) for d in ("NVIDIA", "AMD")]
        watched = {proc.pid}
        while time.time() - started < 3 * 3600:
            alive = False
            if psutil:
                for pid in list(watched):
                    try:
                        p = psutil.Process(pid)
                        if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                            alive = True
                            watched.update(c.pid for c in p.children(recursive=True))
                        else:
                            watched.discard(pid)
                    except psutil.Error:
                        watched.discard(pid)
                # programmi avviati dalle cartelle di installazione (anche se il primo si è già chiuso)
                for p in psutil.process_iter(["exe"]):
                    try:
                        exe = os.path.normcase(p.info["exe"] or "")
                    except psutil.Error:
                        continue
                    if exe and any(exe.startswith(r) for r in roots):
                        watched.add(p.pid)
                        alive = True
            else:
                alive = proc.poll() is None
            if not alive and time.time() - started > 5:
                break
            time.sleep(1.5)
        self.state = {"status": "finished", "vendor": vendor,
                      "msg": "Installazione finita: rileggo il driver installato…"}

    def progress(self):
        s = dict(self.state)
        s.pop("path", None)
        return s
