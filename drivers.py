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
import threading
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

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
    if _nv_products is None:
        _nv_products = {}
        try:   # elenco ufficiale NVIDIA
            root = ET.fromstring(_get(NV_PRODUCTS))
            for lv in root.iter("LookupValue"):
                name, value = lv.findtext("Name"), lv.findtext("Value")
                if name and value:
                    _nv_products.setdefault(name.strip().lower(), value.strip())
        except Exception:
            pass
        if not _nv_products:   # copia dello stesso elenco mantenuta dal progetto nvidia-update
            data = json.loads(_get(NV_GPU_DATA).decode("utf-8"))
            for group in data.values():
                for name, value in group.items():
                    _nv_products.setdefault(name.strip().lower(), str(value))
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
class DriverDownloader:
    def __init__(self):
        self.state = {"status": "idle"}
        self._lock = threading.Lock()

    def start(self, url, vendor):
        ok_hosts = ("https://us.download.nvidia.com/", "https://international.download.nvidia.com/",
                    "https://drivers.amd.com/")
        if not url or not url.startswith(ok_hosts):
            return {"ok": False, "msg": "Indirizzo di download non valido."}
        with self._lock:
            if self.state.get("status") == "downloading":
                return {"ok": False, "msg": "Un download è già in corso."}
            self.state = {"status": "downloading", "done": 0, "total": 0, "vendor": vendor}
        threading.Thread(target=self._run, args=(url, vendor), daemon=True).start()
        return {"ok": True}

    def _run(self, url, vendor):
        folder = os.path.join(os.environ.get("TEMP", "."), "PCBooster", "driver")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, url.rsplit("/", 1)[-1])
        headers = {"User-Agent": UA}
        if vendor == "amd":
            headers["Referer"] = "https://www.amd.com/"   # AMD lo richiede per i download
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                self.state["total"] = int(r.headers.get("Content-Length") or 0)
                while chunk := r.read(1024 * 512):
                    f.write(chunk)
                    self.state["done"] += len(chunk)
            if os.path.getsize(path) < 5_000_000:
                raise IOError("file troppo piccolo")
        except Exception as e:
            self.state = {"status": "error", "msg": f"Download non riuscito ({e.__class__.__name__}). "
                                                    "Riprova o scaricalo dal sito del produttore."}
            return
        self.state = {"status": "installing", "path": path}
        try:
            if IS_WINDOWS:
                subprocess.Popen([path], cwd=folder)
            self.state = {"status": "launched", "msg": "Installazione avviata: segui la finestra del produttore."}
        except Exception as e:
            self.state = {"status": "error", "msg": f"Non riesco ad avviare l'installazione ({e})."}

    def progress(self):
        s = dict(self.state)
        s.pop("path", None)
        return s
