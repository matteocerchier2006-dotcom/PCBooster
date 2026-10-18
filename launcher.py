"""
PC Booster - avvio con i permessi di amministratore senza la finestra di Windows ogni volta.

Registra l'attività di Windows "PC Booster - Avvio", che apre l'app già con i permessi.
Quando apri PC Booster da un collegamento qualsiasi (desktop, barra delle applicazioni,
menu Start), l'app parte senza permessi, avvia l'attività e si chiude: la finestra che vedi
è quella con i permessi. Così nessun collegamento ha bisogno di trucchi.
"""
import os
import sys
import subprocess

IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)
CREATE_NO_WINDOW = 0x08000000
TASK = "PC Booster - Avvio"


def _ps(script, timeout=90):
    if not IS_WINDOWS:
        return ""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, errors="replace", timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return (r.stdout or "").strip()


def _q(s):
    return s.replace("'", "''")


def task_exists():
    if not IS_WINDOWS:
        return False
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def task_target():
    """Il programma che l'attività avvia (per controllare che esista ancora)."""
    if not IS_WINDOWS:
        return ""
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/XML"], capture_output=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        return ""
    raw = r.stdout
    text = raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    import re
    m = re.search(r"<Command>(.*?)</Command>", text, re.S)
    return m.group(1).strip().strip('"') if m else ""


def run_task():
    """Apre PC Booster con i permessi tramite l'attività. True se è partita."""
    if not IS_WINDOWS:
        return False
    target = task_target()
    if not target or not os.path.isfile(target):
        return False
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


SHORTCUT_PS = r"""
function Set-PCBShortcut($path, $exe) {
  $ws = New-Object -ComObject WScript.Shell
  $l = $ws.CreateShortcut($path)
  $l.TargetPath = $exe; $l.Arguments = ''; $l.WorkingDirectory = (Split-Path $exe)
  $l.IconLocation = "$exe,0"; $l.Description = 'PC Booster'; $l.Save()
}
"""


def enable():
    """Crea (o aggiorna) l'attività e i collegamenti sul desktop e nel menu Start. Serve essere amministratore."""
    if not (IS_WINDOWS and IS_FROZEN):
        return {"ok": False, "msg": "Disponibile solo nella versione .exe."}
    exe = _q(sys.executable)
    folder = _q(os.path.dirname(sys.executable))
    out = _ps(
        SHORTCUT_PS +
        f"$a=New-ScheduledTaskAction -Execute '{exe}' -WorkingDirectory '{folder}';"
        "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" -LogonType Interactive -RunLevel Highest;"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances Parallel;"
        f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Principal $p -Settings $s -Force | Out-Null;"
        f"Set-PCBShortcut ([Environment]::GetFolderPath('Desktop')+'\\PC Booster.lnk') '{exe}';"
        f"Set-PCBShortcut ([Environment]::GetFolderPath('Programs')+'\\PC Booster.lnk') '{exe}';"
        "'OK'"
    )
    if out.endswith("OK"):
        return {"ok": True, "msg": "Fatto: PC Booster si apre senza chiedere i permessi da qualsiasi collegamento."}
    return {"ok": False, "msg": "Non ci sono riuscito: servono i permessi di amministratore."}


def disable():
    if not IS_WINDOWS:
        return {"ok": True, "msg": "Disattivato."}
    subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return {"ok": True, "msg": "Fatto: Windows chiederà di nuovo i permessi a ogni avvio."}


def repair_shortcuts():
    """Ripara i collegamenti (desktop, Start, barra delle applicazioni) che puntano a copie vecchie di PC Booster."""
    if not (IS_WINDOWS and IS_FROZEN):
        return 0
    exe = _q(sys.executable)
    out = _ps(
        SHORTCUT_PS +
        "$dirs=@([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'),"
        "[Environment]::GetFolderPath('Programs'),"
        "(Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar'),"
        "(Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\StartMenu'));"
        "$ws=New-Object -ComObject WScript.Shell; $n=0;"
        "foreach($d in $dirs){ if(Test-Path $d){ Get-ChildItem -Path $d -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {"
        "  $l=$ws.CreateShortcut($_.FullName);"
        "  $old = ($l.TargetPath -match '(?i)PC ?Booster[^\\\\]*\\.exe') -or (($l.TargetPath -like '*schtasks.exe') -and ($l.Arguments -like '*PC Booster - Avvio*'));"
        f"  if($old -and ($l.TargetPath -ne '{exe}' -or $l.Arguments)){{ Set-PCBShortcut $_.FullName '{exe}'; $n++ }}"
        "} } }; $n"
    )
    try:
        return int(out.splitlines()[-1]) if out else 0
    except ValueError:
        return 0


def relaunch_command(target):
    """Riga per uno script: riapre l'app con l'attività (senza richiesta) o normalmente."""
    return (f'schtasks /Query /TN "{TASK}" >nul 2>&1 && (schtasks /Run /TN "{TASK}" >nul) || '
            f'(start "" "{target}")')
