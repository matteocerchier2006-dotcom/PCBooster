"""
PC Booster - avvio con i permessi di amministratore senza la finestra di Windows ogni volta.

Registra l'attività di Windows "PC Booster - Avvio", che apre l'app già con i permessi.
Quando apri PC Booster da un collegamento qualsiasi (desktop, barra delle applicazioni,
menu Start), l'app parte senza permessi, avvia l'attività e si chiude: la finestra che vedi
è quella con i permessi. Così nessun collegamento ha bisogno di trucchi.
"""
import os
import sys

import winutil

IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)
TASK = "PC Booster - Avvio"


def _ps(script, timeout=90):
    return winutil.ps(script, timeout)


def _q(s):
    return s.replace("'", "''")


def task_exists():
    if not IS_WINDOWS:
        return False
    return winutil.run(["schtasks", "/Query", "/TN", TASK], timeout=20, log_errors=False).ok


def task_target():
    """Il programma che l'attività avvia (per controllare che esista ancora)."""
    if not IS_WINDOWS:
        return ""
    r = winutil.run(["schtasks", "/Query", "/TN", TASK, "/XML"], timeout=20, log_errors=False)
    if not r.ok:
        return ""
    text = r.out.replace("\x00", "")
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
    return winutil.run(["schtasks", "/Run", "/TN", TASK], timeout=20).ok


SHORTCUT_PS = r"""
function Get-PCBIcon($exe) {
  $ico = Join-Path (Split-Path $exe) 'PCBooster.ico'
  if (Test-Path -LiteralPath $ico) { "$ico,0" } else { "$exe,0" }
}
function Set-PCBShortcut($path, $exe) {
  $ws = New-Object -ComObject WScript.Shell
  $l = $ws.CreateShortcut($path)
  $l.TargetPath = $exe; $l.Arguments = ''; $l.WorkingDirectory = (Split-Path $exe)
  $l.IconLocation = (Get-PCBIcon $exe); $l.Description = 'PC Booster'; $l.Save()
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
    winutil.run(["schtasks", "/Delete", "/TN", TASK, "/F"], timeout=20, log_errors=False)
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
        f"  if($old -and ($l.TargetPath -ne '{exe}' -or $l.Arguments -or $l.IconLocation -ne (Get-PCBIcon '{exe}'))){{ Set-PCBShortcut $_.FullName '{exe}'; $n++ }}"
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
