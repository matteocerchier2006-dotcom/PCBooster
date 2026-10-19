"""
PC Booster - rete per giocare online.

- informazioni sulla connessione (cavo o Wi-Fi, velocità della scheda, segnale Wi-Fi)
- ping verso DNS pubblici e server in Europa (misurato con una connessione TCP: funziona
  anche dove il "ping" classico è bloccato)
- test di velocità (download e upload) con i server di Cloudflare
- prova dei DNS più veloci e cambio dei DNS (con possibilità di tornare indietro)
"""
import os
import re
import sys
import time
import socket
import random
import struct
import statistics
import threading
import ipaddress
import urllib.request

import winutil

IS_WINDOWS = sys.platform == "win32"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PCBooster"


def _ps_json(script, timeout=60):
    if not IS_WINDOWS:
        return None
    return winutil.ps_json(script, timeout)


def _list(x):
    return [i for i in winutil.as_list(x) if i is not None]


def _is_ip(s):
    try:
        ipaddress.ip_address(str(s))
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- connessione
def connection_info():
    if not IS_WINDOWS:
        return {"ok": True, "adapters": [
            {"name": "Ethernet", "desc": "Realtek PCIe 2.5GbE Family Controller", "kind": "Cavo", "speed": "1 Gbps",
             "ip": "192.168.1.23", "gateway": "192.168.1.1", "dns": ["192.168.1.1"], "dns_auto": True,
             "signal": None, "ssid": ""}]}
    rows = _list(_ps_json(
        "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { $a=$_;"
        "$c=Get-NetIPConfiguration -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue;"
        "$d=(Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses;"
        "$ns=(Get-ItemProperty \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\$($a.InterfaceGuid)\" -ErrorAction SilentlyContinue).NameServer;"
        "[pscustomobject]@{ idx=$a.ifIndex; name=$a.Name; desc=$a.InterfaceDescription; speed=\"$($a.LinkSpeed)\";"
        " media=\"$($a.PhysicalMediaType)\"; virt=[bool]$a.Virtual; ip=\"$($c.IPv4Address.IPAddress)\";"
        " gw=\"$($c.IPv4DefaultGateway.NextHop)\"; dns=@($d); ns=\"$ns\" } }"))
    out = winutil.run(["netsh", "wlan", "show", "interfaces"], timeout=15, log_errors=False).out
    m = re.search(r"(?:Segnale|Signal)\s*:\s*(\d+)\s*%", out)
    ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
    wifi = {"signal": int(m.group(1)) if m else None, "ssid": ssid.group(1).strip() if ssid else ""}
    adapters = []
    for r in rows:
        if not isinstance(r, dict) or r.get("virt") or not r.get("gw"):
            continue   # schede virtuali (VPN, macchine virtuali) o senza internet
        media = (r.get("media") or "").lower()
        wireless = "802.11" in media or "wireless" in media or "wi-fi" in (r.get("desc") or "").lower()
        adapters.append({"name": r.get("name"), "desc": r.get("desc"), "kind": "Wi-Fi" if wireless else "Cavo",
                         "speed": r.get("speed"), "ip": r.get("ip"), "gateway": r.get("gw"),
                         "dns": [d for d in _list(r.get("dns")) if _is_ip(d)], "dns_auto": not (r.get("ns") or "").strip(),
                         "signal": wifi.get("signal") if wireless else None, "ssid": wifi.get("ssid") if wireless else "",
                         "index": r.get("idx")})
    return {"ok": True, "adapters": adapters}


# ---------------------------------------------------------------- ping
PING_TARGETS = [
    ("Cloudflare", "1.1.1.1"),
    ("Google", "8.8.8.8"),
    ("Europa · Milano", "dynamodb.eu-south-1.amazonaws.com"),
    ("Europa · Francoforte", "dynamodb.eu-central-1.amazonaws.com"),
    ("Europa · Parigi", "dynamodb.eu-west-3.amazonaws.com"),
    ("Europa · Londra", "dynamodb.eu-west-2.amazonaws.com"),
    ("Stati Uniti · Virginia", "dynamodb.us-east-1.amazonaws.com"),
]


def _tcp_ping(host, port=443, tries=5):
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return None
    times, lost = [], 0
    for _ in range(tries):
        t = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=1.5):
                times.append((time.perf_counter() - t) * 1000)
        except OSError:
            lost += 1
        time.sleep(0.05)
    if not times:
        return {"ms": None, "jitter": None, "loss": 100}
    jitter = statistics.mean(abs(a - b) for a, b in zip(times, times[1:])) if len(times) > 1 else 0
    return {"ms": round(statistics.median(times)), "jitter": round(jitter), "loss": round(lost * 100 / tries)}


def quality(ms):
    if ms is None:
        return "Non raggiungibile"
    return "Ottimo" if ms < 30 else "Buono" if ms < 60 else "Discreto" if ms < 100 else "Lento"


def ping_all():
    if not IS_WINDOWS and os.environ.get("PCB_REAL_NET") != "1":
        demo = [(12, 1), (14, 2), (18, 3), (24, 2), (33, 4), (41, 5), (98, 6)]
        return [{"name": n, "ms": ms, "jitter": j, "loss": 0, "quality": quality(ms)}
                for (n, _h), (ms, j) in zip(PING_TARGETS, demo)]
    results = [None] * len(PING_TARGETS)

    def one(i, name, host):
        r = _tcp_ping(host) or {"ms": None, "jitter": None, "loss": 100}
        r.update({"name": name, "quality": quality(r["ms"])})
        results[i] = r
    threads = [threading.Thread(target=one, args=(i, n, h)) for i, (n, h) in enumerate(PING_TARGETS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ---------------------------------------------------------------- test di velocità
class SpeedTest:
    DOWN = "https://speed.cloudflare.com/__down?bytes={n}"
    UP = "https://speed.cloudflare.com/__up"

    def __init__(self):
        self.state = {"status": "idle"}
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.state.get("status") == "running":
                return {"ok": False, "msg": "Un test è già in corso."}
            self.state = {"status": "running", "phase": "ping", "down": None, "up": None, "ping": None}
        threading.Thread(target=self._run, daemon=True).start()
        return {"ok": True}

    def _run(self):
        if not IS_WINDOWS and os.environ.get("PCB_REAL_NET") != "1":
            for phase, key, val in (("ping", "ping", 11), ("download", "down", 286.4), ("upload", "up", 48.9)):
                self.state["phase"] = phase
                time.sleep(1.2)
                self.state[key] = val
            self.state["status"] = "done"
            return
        try:
            p = _tcp_ping("speed.cloudflare.com")
            self.state["ping"] = p["ms"] if p else None
            self.state["phase"] = "download"
            self.state["live"] = None
            self.state["down"] = self._download()
            self.state["phase"] = "upload"
            self.state["live"] = None
            self.state["up"] = self._upload()
            self.state["status"] = "done"
        except Exception as e:
            winutil._log(f"Test di velocità: {e.__class__.__name__}: {e}")
            self.state = {"status": "error", "msg": f"Test non riuscito ({e.__class__.__name__}): controlla la connessione."}

    def _download(self, limit=8.0):
        """Scarica per circa 8 secondi e misura. Se la connessione cade a metà, usa quello che ha misurato."""
        total, start = 0, time.perf_counter()
        size = 25_000_000
        error = None
        while time.perf_counter() - start < limit:
            req = urllib.request.Request(self.DOWN.format(n=size), headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    while chunk := r.read(256 * 1024):
                        total += len(chunk)
                        secs = time.perf_counter() - start
                        self.state["live"] = round(total * 8 / secs / 1e6, 1) if secs > 0.2 else None
                        if secs > limit:
                            break
            except OSError as e:
                error = e
                break
            size = 100_000_000
        secs = time.perf_counter() - start
        if total < 1_000_000 and error:
            raise error
        return round(total * 8 / max(secs, 0.001) / 1e6, 1)

    def _upload(self, limit=6.0):
        """Invia pacchetti sempre più grandi (parte piccolo: con connessioni lente non si blocca)."""
        size, total, start = 500_000, 0, time.perf_counter()
        error = None
        while time.perf_counter() - start < limit:
            payload = os.urandom(size)
            t0 = time.perf_counter()
            req = urllib.request.Request(self.UP, data=payload, method="POST",
                                         headers={"User-Agent": UA, "Content-Type": "application/octet-stream"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    r.read()
            except OSError as e:
                error = e
                break
            total += len(payload)
            secs = time.perf_counter() - start
            self.state["live"] = round(total * 8 / secs / 1e6, 1)
            if time.perf_counter() - t0 < 1.0 and size < 8_000_000:
                size *= 2
        secs = time.perf_counter() - start
        if not total and error:
            raise error
        return round(total * 8 / max(secs, 0.001) / 1e6, 1)

    def progress(self):
        return dict(self.state)


# ---------------------------------------------------------------- DNS
DNS_PROVIDERS = [
    {"id": "cloudflare", "name": "Cloudflare", "servers": ["1.1.1.1", "1.0.0.1"], "desc": "Veloce e attento alla privacy."},
    {"id": "google", "name": "Google", "servers": ["8.8.8.8", "8.8.4.4"], "desc": "Veloce e affidabile."},
    {"id": "quad9", "name": "Quad9", "servers": ["9.9.9.9", "149.112.112.112"], "desc": "Blocca i siti pericolosi conosciuti."},
    {"id": "adguard", "name": "AdGuard", "servers": ["94.140.14.14", "94.140.15.15"], "desc": "Blocca pubblicità e tracciamento."},
    {"id": "opendns", "name": "OpenDNS", "servers": ["208.67.222.222", "208.67.220.220"], "desc": "Di Cisco, molto diffuso."},
]
TEST_DOMAINS = ["google.com", "youtube.com", "steampowered.com", "epicgames.com", "discord.com", "twitch.tv"]


def _dns_query(server, domain, timeout=1.5):
    qid = random.randint(0, 65535)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in domain.split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t = time.perf_counter()
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(512)
        if struct.unpack(">H", data[:2])[0] != qid:
            raise OSError("risposta non valida")
        return (time.perf_counter() - t) * 1000


def benchmark_dns(current=None):
    """Tempo medio di risposta di ogni DNS (più basso = pagine e giochi si collegano prima)."""
    providers = [dict(p) for p in DNS_PROVIDERS]
    if current:
        providers.insert(0, {"id": "current", "name": "Quello che usi adesso", "servers": current[:2],
                             "desc": ", ".join(current[:2])})
    if not IS_WINDOWS and os.environ.get("PCB_REAL_NET") != "1":
        demo = {"current": 38, "cloudflare": 9, "google": 13, "quad9": 21, "adguard": 27, "opendns": 24}
        for p in providers:
            p["ms"] = demo.get(p["id"])
    else:
        def one(p):
            times = []
            for d in TEST_DOMAINS:
                try:
                    times.append(_dns_query(p["servers"][0], d))
                except OSError:
                    pass
            p["ms"] = round(statistics.mean(times)) if len(times) >= len(TEST_DOMAINS) // 2 else None
        threads = [threading.Thread(target=one, args=(p,)) for p in providers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    ranked = sorted([p for p in providers if p.get("ms") is not None], key=lambda p: p["ms"])
    if ranked:
        ranked[0]["best"] = True
    return providers


def _active_adapters():
    return [a for a in connection_info()["adapters"] if a.get("index") is not None]


def _apply_dns(changes):
    """changes: [(indice scheda, [server] oppure None = automatici)]. Restituisce quante schede ha cambiato."""
    lines = []
    for idx, servers in changes:
        idx = int(idx)
        if servers:
            lst = ",".join(f"'{x}'" for x in servers if _is_ip(x))
            cmd = f"Set-DnsClientServerAddress -InterfaceIndex {idx} -ServerAddresses @({lst}) -ErrorAction Stop"
        else:
            cmd = f"Set-DnsClientServerAddress -InterfaceIndex {idx} -ResetServerAddresses -ErrorAction Stop"
        lines.append(f"try {{ {cmd}; $ok++ }} catch {{ $err += $_.Exception.Message }}")
    r = winutil.ps_run("$ok=0; $err=@();" + ";".join(lines) + "; \"$ok|$($err -join ' / ')\"", 90)
    winutil.run(["ipconfig", "/flushdns"], timeout=20, log_errors=False)
    ok_count, _, err = (r.out.splitlines()[-1] if r.out else "0|").partition("|")
    if err:
        winutil._log(f"DNS: {err}")
    try:
        return int(ok_count)
    except ValueError:
        return 0


def set_dns(provider_id):
    """Cambia i DNS di tutte le connessioni attive. Restituisce anche come tornare indietro."""
    if provider_id == "auto":
        servers = None
    else:
        p = next((p for p in DNS_PROVIDERS if p["id"] == provider_id), None)
        if not p:
            return {"ok": False, "msg": "DNS sconosciuto."}
        servers = p["servers"]
    if not IS_WINDOWS:
        return {"ok": True, "msg": "Demo: DNS cambiati.", "undo": {"type": "dns", "prev": [{"index": 1, "servers": []}]}}
    adapters = _active_adapters()
    if not adapters:
        return {"ok": False, "msg": "Nessuna connessione attiva."}
    prev = [{"index": a["index"], "servers": [] if a["dns_auto"] else a["dns"]} for a in adapters]
    done = _apply_dns([(a["index"], servers) for a in adapters])
    if not done:
        return {"ok": False, "msg": "Non riesco a cambiare i DNS: servono i permessi di amministratore."}
    msg = "DNS cambiati." if servers else "DNS tornati automatici."
    if done < len(adapters):
        msg += f" (su {done} connessioni su {len(adapters)})"
    return {"ok": True, "msg": msg, "undo": {"type": "dns", "prev": prev}}


def restore_dns(prev):
    if not IS_WINDOWS:
        return {"ok": True, "msg": "Demo: DNS ripristinati."}
    changes = []
    for a in prev or []:
        try:
            changes.append((int(a["index"]), [x for x in a.get("servers") or [] if _is_ip(x)] or None))
        except (KeyError, TypeError, ValueError):
            continue
    if not changes:
        return {"ok": False, "msg": "Non so come erano prima i DNS."}
    if not _apply_dns(changes):
        return {"ok": False, "msg": "Non riesco a ripristinare i DNS: la connessione potrebbe essere cambiata."}
    return {"ok": True, "msg": "DNS ripristinati come prima."}
