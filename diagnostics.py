"""
PC Booster - "Segnala un problema".

- Scrive gli errori (del programma e dell'interfaccia) in %APPDATA%\\PCBooster\\errori.log
- Prepara un report .zip sul desktop con: versione, info del PC, diario degli aggiornamenti,
  errori e cronologia. Il nome utente viene nascosto nei percorsi.
"""
import os
import sys
import json
import time
import zipfile
import platform
import threading
import traceback
import urllib.parse

import updater

IS_WINDOWS = sys.platform == "win32"


def _dir():
    return os.path.dirname(updater._settings_path())


def _errlog():
    return os.path.join(_dir(), "errori.log")


def log_error(text):
    try:
        path = _errlog()
        if os.path.exists(path) and os.path.getsize(path) > 300_000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} (versione {updater.app_version()})\n{text.rstrip()}\n")
    except OSError:
        pass


def install_hooks():
    """Ogni errore non gestito finisce in errori.log invece di sparire."""
    def hook(exc_type, exc, tb):
        log_error("".join(traceback.format_exception(exc_type, exc, tb)))

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            log_error(f"[thread {args.thread.name if args.thread else '?'}]\n" +
                      "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    sys.excepthook = hook
    threading.excepthook = thread_hook


def _anon(text):
    """Nasconde il nome utente e il nome del PC."""
    for value in {os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", ""),
                  os.path.basename(os.path.expanduser("~"))}:
        if value and len(value) > 1:
            text = text.replace(value, "<utente>" if value != os.environ.get("COMPUTERNAME") else "<pc>")
    return text


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def make_report(system_info):
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desktop):
        desktop = _dir()
    path = os.path.join(desktop, f"PC Booster report {time.strftime('%Y-%m-%d %H-%M')}.zip")
    s = updater.load_settings()
    prefs = {k: v for k, v in s.items() if k not in ("notified", "game_mode_close")}
    info = [
        f"PC Booster {updater.app_version()}",
        f"Eseguibile: {sys.executable}",
        f"Windows: {system_info.get('os', platform.platform())}",
        f"Processore: {system_info.get('cpu', '')}",
        f"Scheda video: {system_info.get('gpu', '')}",
        f"RAM: {system_info.get('ram_gb', '')} GB",
        f"Amministratore: {system_info.get('admin')}",
        f"Data: {time.strftime('%Y-%m-%d %H:%M')}",
        "", "Impostazioni:", json.dumps(prefs, ensure_ascii=False, indent=1),
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.txt", _anon("\n".join(info)))
        for name in ("aggiornamenti.log", "errori.log", "cronologia.json"):
            content = _read(os.path.join(_dir(), name))
            if content:
                z.writestr(name, _anon(content))
    if IS_WINDOWS:
        import winutil
        winutil.start(["explorer", "/select,", path], hidden=False)
    return {"ok": True, "path": path,
            "msg": "Report pronto sul desktop. Il tuo nome utente e il nome del PC sono nascosti."}


def issue_url(system_info):
    repo = updater.GITHUB_REPO or "matteocerchier2006-dotcom/PCBooster"
    body = (f"**Cosa è successo?**\n\n\n**Cosa stavi facendo?**\n\n\n"
            f"---\nPC Booster {updater.app_version()} · {system_info.get('os', '')}\n"
            f"(Trascina qui il file \"PC Booster report\" che trovi sul desktop.)")
    q = urllib.parse.urlencode({"title": "Problema: ", "body": body})
    return f"https://github.com/{repo}/issues/new?{q}"
