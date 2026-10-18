"""
PC Booster - sicurezza con Microsoft Defender (l'antivirus già dentro Windows).

- stato della protezione (tempo reale, definizioni, ultimo controllo, firewall, altri antivirus)
- controllo veloce / completo / offline, aggiornamento delle definizioni
- elenco delle minacce trovate e rimozione
"""
import os
import sys
import json
import time
import threading
import subprocess

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
MPCMD = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Windows Defender", "MpCmdRun.exe")


def _ps(script, timeout=120):
    if not IS_WINDOWS:
        return ""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return (r.stdout or "").strip()


def _json(script, timeout=120):
    out = _ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout)
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError:
        return None


def _as_list(x):
    return [] if x is None else x if isinstance(x, list) else [x]


SEVERITY = {0: "Sconosciuta", 1: "Bassa", 2: "Media", 4: "Alta", 5: "Grave"}

DEMO_STATUS = {
    "ok": True, "defender_active": True, "realtime": True, "mode": "Normal", "sig_age_days": 0,
    "sig_updated": "2026-10-18 09:12", "sig_version": "1.421.118.0", "quick_age_days": 3, "full_age_days": 41,
    "tamper": True, "other_av": [], "firewall": [{"name": "Dominio", "on": True}, {"name": "Privata", "on": True},
                                                 {"name": "Pubblica", "on": True}],
}


class Security:
    def __init__(self):
        self.scan = {"status": "idle"}
        self._demo_threats = []

    # ---------------- stato ----------------
    def status(self):
        if not IS_WINDOWS:
            return dict(DEMO_STATUS)
        d = _json(
            "$s=Get-MpComputerStatus -ErrorAction SilentlyContinue;"
            "$av=@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue"
            " | ForEach-Object { [pscustomobject]@{ name=$_.displayName; state=[int]$_.productState } });"
            "$fw=@(Get-NetFirewallProfile -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ name=\"$($_.Name)\"; on=[bool]$_.Enabled } });"
            "[pscustomobject]@{ have=[bool]$s; rt=$s.RealTimeProtectionEnabled; av=$s.AntivirusEnabled; mode=\"$($s.AMRunningMode)\";"
            "sigAge=$s.AntivirusSignatureAge; sigVer=$s.AntivirusSignatureVersion;"
            "sigUpd=$(if($s.AntivirusSignatureLastUpdated){$s.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd HH:mm')});"
            "quickAge=$s.QuickScanAge; fullAge=$s.FullScanAge; tamper=$s.IsTamperProtected; av3=$av; fw=$fw }", 60)
        if not d:
            return {"ok": False, "msg": "Non riesco a leggere lo stato di Microsoft Defender."}
        others = []
        for p in _as_list(d.get("av3")):
            name = p.get("name") or ""
            if "defender" in name.lower():
                continue
            st = p.get("state") or 0
            others.append({"name": name, "on": ((st >> 8) & 0xFF) in (0x10, 0x11), "updated": (st & 0xFF) == 0})
        names = {"Domain": "Dominio", "Private": "Privata", "Public": "Pubblica"}
        age = lambda v: None if v is None or v in (4294967295, 65535) else int(v)
        return {
            "ok": bool(d.get("have")), "defender_active": bool(d.get("av")) and d.get("mode") == "Normal",
            "realtime": bool(d.get("rt")), "mode": d.get("mode") or "", "sig_age_days": age(d.get("sigAge")),
            "sig_updated": d.get("sigUpd") or "", "sig_version": d.get("sigVer") or "",
            "quick_age_days": age(d.get("quickAge")), "full_age_days": age(d.get("fullAge")),
            "tamper": bool(d.get("tamper")), "other_av": others,
            "firewall": [{"name": names.get(f.get("name"), f.get("name")), "on": bool(f.get("on"))}
                         for f in _as_list(d.get("fw"))],
        }

    # ---------------- minacce ----------------
    def threats(self):
        if not IS_WINDOWS:
            return list(self._demo_threats)
        threats = _as_list(_json(
            "Get-MpThreat -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ id=[string]$_.ThreatID;"
            " name=$_.ThreatName; sev=[int]$_.SeverityID; active=[bool]$_.IsActive; ran=[bool]$_.DidThreatExecute } }", 60))
        det = _as_list(_json(
            "Get-MpThreatDetection -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ id=[string]$_.ThreatID;"
            " when=$(if($_.InitialDetectionTime){$_.InitialDetectionTime.ToString('yyyy-MM-dd HH:mm')}); res=@($_.Resources);"
            " status=[int]$_.ThreatStatusID } }", 60))
        by_id = {}
        for d in det:
            by_id.setdefault(d.get("id"), d)
        out = []
        for t in threats:
            d = by_id.get(t.get("id"), {})
            res = [r.split("_", 1)[-1] for r in _as_list(d.get("res")) if r][:3]
            out.append({"id": t.get("id"), "name": t.get("name"), "severity": SEVERITY.get(t.get("sev"), "Sconosciuta"),
                        "active": bool(t.get("active")), "ran": bool(t.get("ran")), "when": d.get("when") or "",
                        "files": res})
        out.sort(key=lambda x: (not x["active"], x["when"]), reverse=False)
        return out

    def remove_threats(self):
        if not IS_WINDOWS:
            self._demo_threats = [dict(t, active=False) for t in self._demo_threats]
            return {"ok": True, "msg": "Minacce rimosse."}
        _ps("Remove-MpThreat -ErrorAction SilentlyContinue", 600)
        left = [t for t in self.threats() if t["active"]]
        if left:
            return {"ok": False, "msg": "Non tutte le minacce sono state rimosse: prova un controllo offline."}
        return {"ok": True, "msg": "Minacce rimosse: sono state messe in quarantena o eliminate."}

    # ---------------- controlli ----------------
    def start_scan(self, kind):
        cmds = {"quick": "Start-MpScan -ScanType QuickScan", "full": "Start-MpScan -ScanType FullScan"}
        if kind not in cmds:
            return {"ok": False, "msg": "Tipo di controllo non valido."}
        if self.scan.get("status") == "running":
            return {"ok": False, "msg": "Un controllo è già in corso."}
        self.scan = {"status": "running", "kind": kind, "started": time.time()}

        def run():
            if IS_WINDOWS:
                try:
                    _ps(cmds[kind] + " -ErrorAction Stop; 'OK'", 24 * 3600)
                except subprocess.TimeoutExpired:
                    pass
            else:
                time.sleep(6)
                self._demo_threats = [{"id": "2147735503", "name": "PUA:Win32/Presenoker", "severity": "Bassa",
                                       "active": True, "ran": False, "when": time.strftime("%Y-%m-%d %H:%M"),
                                       "files": [r"C:\Users\Demo\Downloads\FreeGameBooster_setup.exe"]}]
            if self.scan.get("status") == "running":
                found = [t for t in self.threats() if t["active"]]
                self.scan = {"status": "done", "kind": kind, "found": len(found),
                             "seconds": int(time.time() - self.scan["started"])}
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def scan_progress(self):
        s = dict(self.scan)
        if s.get("status") == "running":
            s["elapsed"] = int(time.time() - s["started"])
        return s

    def cancel_scan(self):
        if IS_WINDOWS and os.path.exists(MPCMD):
            subprocess.run([MPCMD, "-Cancel"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        self.scan = {"status": "cancelled"}
        return {"ok": True, "msg": "Controllo annullato."}

    def update_signatures(self):
        if not IS_WINDOWS:
            time.sleep(1)
            return {"ok": True, "msg": "Definizioni aggiornate."}
        out = _ps("try { Update-MpSignature -ErrorAction Stop; 'OK' } catch { $_.Exception.Message }", 900)
        if out.endswith("OK"):
            return {"ok": True, "msg": "Definizioni dei virus aggiornate."}
        return {"ok": False, "msg": "Aggiornamento non riuscito: controlla la connessione e riprova."}

    def offline_scan(self):
        """Riavvia il PC e controlla prima che parta Windows (trova i virus che si nascondono)."""
        if not IS_WINDOWS:
            return {"ok": True, "msg": "Demo: il PC si riavvierebbe per il controllo offline."}
        _ps("Start-MpWDOScan", 60)
        return {"ok": True, "msg": "Il PC si riavvia tra poco per il controllo offline (circa 15 minuti)."}

    def enable_realtime(self):
        if not IS_WINDOWS:
            return {"ok": True, "msg": "Protezione in tempo reale attivata."}
        _ps("Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction SilentlyContinue", 60)
        ok = self.status().get("realtime")
        return {"ok": bool(ok), "msg": "Protezione in tempo reale attivata." if ok else
                "Non riesco ad attivarla: aprila da Sicurezza di Windows (potrebbe esserci un altro antivirus)."}

    def enable_firewall(self):
        if not IS_WINDOWS:
            return {"ok": True, "msg": "Firewall attivato."}
        _ps("Set-NetFirewallProfile -All -Enabled True -ErrorAction SilentlyContinue", 60)
        return {"ok": True, "msg": "Firewall attivato su tutte le reti."}

    @staticmethod
    def open_windows_security():
        if IS_WINDOWS:
            os.startfile("windowsdefender:")
        return True


# ======================================================================
# Salute dei dischi (SSD e hard disk)
# ======================================================================
DEMO_DISKS = [
    {"name": "Samsung SSD 980 1TB", "kind": "SSD NVMe", "size_gb": 932, "letters": "C:", "health": "Healthy",
     "temp": 41, "wear": 7, "hours": 6120, "errors": 0},
    {"name": "Crucial MX500 2TB", "kind": "SSD", "size_gb": 1863, "letters": "D:", "health": "Healthy",
     "temp": 35, "wear": 23, "hours": 14250, "errors": 0},
    {"name": "WDC WD10EZEX-08WN4A0", "kind": "HDD", "size_gb": 932, "letters": "E:", "health": "Warning",
     "temp": 52, "wear": None, "hours": 41800, "errors": 12},
]


def disk_health():
    if not IS_WINDOWS:
        disks = [dict(d) for d in DEMO_DISKS]
    else:
        rows = _as_list(_json(
            "Get-PhysicalDisk | ForEach-Object { $d=$_; $r=$d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue;"
            "$l=(Get-Partition -DiskNumber $d.DeviceId -ErrorAction SilentlyContinue | Where-Object DriveLetter |"
            " ForEach-Object { \"$($_.DriveLetter):\" }) -join ' ';"
            "[pscustomobject]@{ name=$d.FriendlyName; media=\"$($d.MediaType)\"; bus=\"$($d.BusType)\"; size=[double]$d.Size;"
            " health=\"$($d.HealthStatus)\"; temp=$r.Temperature; wear=$r.Wear; hours=$r.PowerOnHours;"
            " errors=([int]$r.ReadErrorsUncorrected + [int]$r.WriteErrorsUncorrected); letters=$l } }", 90))
        disks = []
        for r in rows:
            media, bus = r.get("media") or "", r.get("bus") or ""
            kind = "SSD NVMe" if bus == "NVMe" else "SSD" if media == "SSD" else "HDD" if media == "HDD" else \
                "USB" if bus == "USB" else "Disco"
            disks.append({"name": (r.get("name") or "Disco").strip(), "kind": kind,
                          "size_gb": round((r.get("size") or 0) / 1024 ** 3), "letters": r.get("letters") or "",
                          "health": r.get("health") or "", "temp": r.get("temp") or None,
                          "wear": r.get("wear"), "hours": r.get("hours"), "errors": r.get("errors") or 0})
    for d in disks:
        hot = 60 if d["kind"].startswith("SSD") else 55
        if d["health"] == "Unhealthy":
            d["status"], d["note"] = "bad", "Il disco segnala un guasto: fai subito una copia dei tuoi file."
        elif d["health"] == "Warning" or (d.get("errors") or 0) > 0:
            d["status"], d["note"] = "warn", "Il disco segnala dei problemi: conviene fare una copia dei file importanti."
        elif d.get("wear") is not None and d["wear"] >= 80:
            d["status"], d["note"] = "warn", "L'SSD è vicino alla fine della sua vita: pensa a sostituirlo."
        elif d.get("temp") and d["temp"] >= hot:
            d["status"], d["note"] = "warn", "Il disco è molto caldo: controlla l'aerazione del PC."
        else:
            d["status"], d["note"] = "ok", "Tutto a posto."
        d["life"] = None if d.get("wear") is None else max(0, 100 - int(d["wear"]))
    return disks
