"""
PC Booster - utilizzo del PC nel tempo (per i grafici).

Ogni 2 secondi annota: processore, RAM, scheda video, disco (lettura/scrittura) e rete (download/upload).
Tiene l'ultima ora in memoria (niente file, niente internet). Costa pochissimo: sono letture di
contatori che Windows aggiorna comunque.

La scheda video si legge con i contatori di prestazione di Windows (gli stessi di Gestione attività)
usando i nomi inglesi dei contatori, che funzionano su Windows in qualsiasi lingua.
"""
import os
import sys
import time
import math
import threading
from collections import deque

IS_WINDOWS = sys.platform == "win32"

try:
    import psutil
except ImportError:
    psutil = None

INTERVAL = 2.0
KEEP = int(3600 / INTERVAL)          # un'ora di dati
SERIES = ("cpu", "ram", "gpu", "disk_r", "disk_w", "net_d", "net_u")


# ---------------------------------------------------------------- scheda video (PDH di Windows)
class GpuCounter:
    """Utilizzo 3D della scheda video in %, come la colonna "GPU" di Gestione attività."""
    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2

    def __init__(self):
        self.ok = False
        self._query = None
        self._counter = None
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes
            self._ct = ctypes
            self._pdh = ctypes.windll.pdh

            class FMT(ctypes.Structure):
                _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

            class ITEM(ctypes.Structure):
                _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", FMT)]
            self._ITEM = ITEM
            self._pdh.PdhOpenQueryW.restype = wintypes.LONG
            self._pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
            self._pdh.PdhCollectQueryData.restype = wintypes.LONG
            self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
            q, c = wintypes.HANDLE(), wintypes.HANDLE()
            if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
                return
            if self._pdh.PdhAddEnglishCounterW(q, r"\GPU Engine(*)\Utilization Percentage", 0, ctypes.byref(c)) != 0:
                self._pdh.PdhCloseQuery(q)
                return
            self._query, self._counter = q, c
            self._pdh.PdhCollectQueryData(q)        # la prima lettura serve solo a partire
            self.ok = True
        except Exception:
            self.ok = False

    def read(self):
        """Percentuale 0-100, oppure None se non disponibile."""
        if not self.ok:
            return None
        ct = self._ct
        try:
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return None
            size, count = ct.c_ulong(0), ct.c_ulong(0)
            r = self._pdh.PdhGetFormattedCounterArrayW(self._counter, self.PDH_FMT_DOUBLE, ct.byref(size),
                                                      ct.byref(count), None)
            if (r & 0xFFFFFFFF) != self.PDH_MORE_DATA or not size.value:
                return 0.0 if r == 0 else None
            buf = (ct.c_byte * size.value)()
            if self._pdh.PdhGetFormattedCounterArrayW(self._counter, self.PDH_FMT_DOUBLE, ct.byref(size),
                                                      ct.byref(count), buf) != 0:
                return None
            items = ct.cast(buf, ct.POINTER(self._ITEM))
            per_engine = {}
            for i in range(count.value):
                it = items[i]
                if it.FmtValue.CStatus not in (0, 1) or not it.szName:
                    continue
                name = it.szName
                if "engtype_3D" not in name:
                    continue
                # es. "pid_1234_luid_0x0_0xD1A5_phys_0_eng_0_engtype_3D": stessa scheda e stesso motore
                # (la parte da "luid_" in poi) → si sommano i processi
                key = name[name.find("luid_"):] if "luid_" in name else name
                per_engine[key] = per_engine.get(key, 0.0) + max(0.0, it.FmtValue.doubleValue)
            if not per_engine:
                return 0.0
            return round(min(100.0, max(per_engine.values())), 1)
        except Exception:
            return None


# ---------------------------------------------------------------- raccolta
class Monitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._t = deque(maxlen=KEEP)
        self._data = {k: deque(maxlen=KEEP) for k in SERIES}
        self._thread = None
        self._gpu = None
        self._prev = None
        self._procs = {}
        self._plock = threading.RLock()
        self._ncpu = (psutil.cpu_count() if psutil else 1) or 1
        self._demo_phase = 0.0
        self.active = True          # False quando la finestra è nascosta vicino all'orologio: niente misure

    def set_active(self, on):
        self.active = bool(on)
        if on:
            self._prev = None       # dopo la pausa si riparte da capo (niente picchi finti)

    def start(self):
        with self._lock:
            if self._thread or psutil is None:
                return
            self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
            self._thread.start()

    def _loop(self):
        self._gpu = GpuCounter()
        psutil.cpu_percent(None)
        try:
            self.top_processes()        # prepara le misure dei programmi: la pagina Utilizzo le mostra subito
        except Exception:
            pass
        self._prev = self._io()
        while True:
            time.sleep(INTERVAL)
            if not self.active:
                continue
            try:
                if self._prev is None:
                    self._prev = self._io()
                    psutil.cpu_percent(None)
                    continue
                self._sample()
            except Exception:
                pass

    @staticmethod
    def _io():
        try:
            d = psutil.disk_io_counters()
        except Exception:
            d = None
        try:
            n = psutil.net_io_counters()
        except Exception:
            n = None
        return (time.time(), d, n)

    def _sample(self):
        now, d, n = self._io()
        pt, pd, pn = self._prev
        self._prev = (now, d, n)
        dt = max(0.5, now - pt)
        rate = lambda a, b: max(0.0, (a - b) / dt)
        disk_r = disk_w = net_d = net_u = None
        if d and pd:
            disk_r = round(rate(d.read_bytes, pd.read_bytes) / 1e6, 2)      # MB/s
            disk_w = round(rate(d.write_bytes, pd.write_bytes) / 1e6, 2)
        if n and pn:
            net_d = round(rate(n.bytes_recv, pn.bytes_recv) * 8 / 1e6, 2)    # Mbps
            net_u = round(rate(n.bytes_sent, pn.bytes_sent) * 8 / 1e6, 2)
        gpu = self._gpu.read() if self._gpu else None
        if gpu is None and not IS_WINDOWS:            # modalità demo: un andamento finto ma credibile
            self._demo_phase += 0.15
            gpu = round(max(0.0, 35 + 25 * math.sin(self._demo_phase) + 8 * math.sin(self._demo_phase * 3.7)), 1)
        row = {"cpu": round(psutil.cpu_percent(None), 1), "ram": round(psutil.virtual_memory().percent, 1),
               "gpu": gpu, "disk_r": disk_r, "disk_w": disk_w, "net_d": net_d, "net_u": net_u}
        with self._lock:
            self._t.append(round(now, 1))
            for k in SERIES:
                self._data[k].append(row[k])

    # ---------------------------------------------------------------- lettura per l'interfaccia
    def history(self, seconds=300, max_points=300):
        """Gli ultimi `seconds` secondi, al massimo `max_points` punti (le medie se sono di più)."""
        self.start()
        try:
            seconds = max(30, min(3600, int(seconds)))
        except (TypeError, ValueError):
            seconds = 300
        now = time.time()
        with self._lock:
            idx = [i for i, t in enumerate(self._t) if t >= now - seconds]
            t = [self._t[i] for i in idx]
            cols = {k: [self._data[k][i] for i in idx] for k in SERIES}
        step = max(1, math.ceil(len(t) / max_points))
        if step > 1:
            def avg(vals):
                v = [x for x in vals if x is not None]
                return round(sum(v) / len(v), 2) if v else None
            t = [t[i + len(t[i:i + step]) - 1] for i in range(0, len(t), step)]
            cols = {k: [avg(v[i:i + step]) for i in range(0, len(v), step)] for k, v in cols.items()}
        return {"ok": True, "now": round(now, 1), "seconds": seconds, "interval": INTERVAL * step, "t": t,
                "gpu_available": any(x is not None for x in cols["gpu"]) or (self._gpu is not None and self._gpu.ok)
                                 or not IS_WINDOWS, **cols}

    def top_processes(self, limit=8):
        """I programmi che in questo momento usano più processore e memoria (raggruppati per nome)."""
        with self._plock:
            return self._top_processes(limit)

    def _top_processes(self, limit=8, retry=True):
        if psutil is None:
            return {"ok": False, "cpu": [], "mem": []}
        first = not self._procs
        seen, groups = set(), {}
        me = os.getpid()
        for p in psutil.process_iter(["name", "memory_info"]):
            pid = p.pid
            if pid in (0, 4):
                continue
            seen.add(pid)
            proc = self._procs.get(pid)
            if proc is None:
                proc = self._procs[pid] = p
                try:
                    proc.cpu_percent(None)      # prima lettura: da qui in poi la percentuale è vera
                except psutil.Error:
                    pass
                cpu = 0.0
            else:
                try:
                    cpu = proc.cpu_percent(None) / self._ncpu
                except psutil.Error:
                    continue
            name = (p.info.get("name") or "").strip()
            if not name or name.lower() in ("system idle process", "idle", "memcompression", "registry"):
                continue
            mem = p.info["memory_info"].rss if p.info.get("memory_info") else 0
            g = groups.setdefault(name.lower(), {"name": name, "cpu": 0.0, "mem": 0, "count": 0, "me": False})
            g["cpu"] += cpu
            g["mem"] += mem
            g["count"] += 1
            g["me"] = g["me"] or pid == me
        for pid in list(self._procs):
            if pid not in seen:
                del self._procs[pid]
        if first and retry:
            # la prima volta le percentuali sono tutte 0: si aspetta un attimo e si rimisura (una volta sola)
            time.sleep(0.8)
            return self._top_processes(limit, retry=False)
        rows = [{"name": g["name"], "cpu": round(min(100.0, g["cpu"]), 1), "mem_mb": round(g["mem"] / 1024 ** 2),
                 "count": g["count"], "me": g["me"]} for g in groups.values()]
        rows.sort(key=lambda r: (r["cpu"], r["mem_mb"]), reverse=True)
        by_cpu = rows[:limit]
        by_mem = sorted(rows, key=lambda r: r["mem_mb"], reverse=True)[:limit]
        return {"ok": True, "cpu": by_cpu, "mem": by_mem}

    def latest(self, key):
        """Ultimo valore registrato (None se non ce n'è ancora uno)."""
        with self._lock:
            d = self._data.get(key)
            return d[-1] if d else None

    def gpu_now(self):
        """Ultimo valore dell'utilizzo della scheda video (None se non disponibile)."""
        self.start()
        with self._lock:
            for v in reversed(self._data["gpu"]):
                if v is not None:
                    return v
        return None


MONITOR = Monitor()
