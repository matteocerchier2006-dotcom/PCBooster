"""
PC Booster - avvio senza la richiesta dei permessi ogni volta.

Registra un'attività di Windows "PC Booster - Avvio" che apre l'app già con i permessi
di amministratore, e crea sul desktop il collegamento "PC Booster" che la avvia.
È lo stesso sistema usato da molti programmi di ottimizzazione.
La stessa attività serve anche per riaprire l'app dopo un aggiornamento.
"""
import os
import sys
import subprocess

IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)
CREATE_NO_WINDOW = 0x08000000
TASK = "PC Booster - Avvio"


def _ps(script, timeout=60):
    if not IS_WINDOWS:
        return ""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, errors="replace", timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return (r.stdout or "").strip()


def _q(s):
    return s.replace("'", "''")


def shortcut_path():
    return os.path.join(os.path.expanduser("~"), "Desktop", "PC Booster.lnk")


def task_exists():
    if not IS_WINDOWS:
        return False
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def enable():
    """Crea (o aggiorna) l'attività e il collegamento sul desktop. Serve essere amministratore."""
    if not (IS_WINDOWS and IS_FROZEN):
        return {"ok": False, "msg": "Disponibile solo nella versione .exe."}
    exe = _q(sys.executable)
    folder = _q(os.path.dirname(sys.executable))
    out = _ps(
        f"$a=New-ScheduledTaskAction -Execute '{exe}' -WorkingDirectory '{folder}';"
        "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" -LogonType Interactive -RunLevel Highest;"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances Parallel;"
        f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Principal $p -Settings $s -Force | Out-Null;"
        "$ws=New-Object -ComObject WScript.Shell;"
        "$l=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\PC Booster.lnk');"
        "$l.TargetPath=\"$env:WINDIR\\System32\\schtasks.exe\";"
        f"$l.Arguments='/Run /TN \"{TASK}\"';"
        f"$l.IconLocation='{exe},0';"
        "$l.WindowStyle=7; $l.Description='Apre PC Booster senza chiedere i permessi'; $l.Save(); 'OK'"
    )
    if out.endswith("OK"):
        return {"ok": True, "msg": "Fatto: usa il collegamento \"PC Booster\" sul desktop."}
    return {"ok": False, "msg": "Non sono riuscito a creare il collegamento: servono i permessi di amministratore."}


def disable():
    if not IS_WINDOWS:
        return {"ok": True, "msg": "Disattivato."}
    subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
    try:
        os.remove(shortcut_path())
    except OSError:
        pass
    return {"ok": True, "msg": "Fatto: Windows chiederà di nuovo i permessi a ogni avvio."}


def relaunch_command(target):
    """Riga per lo script di aggiornamento: riapre l'app con l'attività (senza richiesta) o normalmente."""
    return (f'schtasks /Query /TN "{TASK}" >nul 2>&1 && (schtasks /Run /TN "{TASK}" >nul) || '
            f'(start "" "{target}")')
