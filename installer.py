"""
PC Booster - installazione in C:\\Program Files\\PC Booster.

Program Files è una cartella protetta: solo l'amministratore può modificarne i file.
Così nessun programma (o virus) senza permessi può sostituire PC Booster, che parte
con i permessi di amministratore.

Al primo avvio da un'altra cartella (Download, Documenti…):
  1) si copia in Program Files
  2) si riapre da lì
  3) la copia nuova cancella quella vecchia e ripara collegamenti e attività
"""
import os
import sys
import time
import shutil
import threading

import updater
import launcher
import winutil

IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)

INSTALL_DIR = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PC Booster")
TARGET = os.path.join(INSTALL_DIR, "PCBooster.exe")


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_installed_copy():
    return _same(sys.executable, TARGET)


def needs_install():
    return IS_WINDOWS and IS_FROZEN and not is_installed_copy() and "--portable" not in sys.argv


def install_and_launch():
    """Copia l'exe in Program Files e lo avvia. True se la copia installata è partita."""
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        if os.path.exists(TARGET):
            # una versione installata c'è già: la mette da parte (anche se fosse in uso)
            os.replace(TARGET, f"{TARGET}.{int(time.time())}.old")
        shutil.copy2(sys.executable, TARGET)
    except Exception as e:
        updater.log(f"Installazione in Program Files non riuscita: {e}")
        return False
    updater.save_settings({"old_install_path": sys.executable})
    updater.log(f"Installato in {TARGET} (prima era in {sys.executable})")
    if not winutil.start([TARGET], cwd=INSTALL_DIR, hidden=False):
        updater.log("Avvio della copia installata non riuscito")
        return False
    return True


def post_start_maintenance(api, tray_mod):
    """Gira in background a ogni avvio della copia installata."""
    def run():
        s = updater.load_settings()
        # 1) elimina la copia vecchia (aspetta che si chiuda)
        old = s.get("old_install_path")
        if old and not _same(old, sys.executable):
            for _ in range(30):
                try:
                    if os.path.exists(old):
                        os.remove(old)
                    updater.log(f"Copia vecchia eliminata: {old}")
                    updater.save_settings({"old_install_path": ""})
                    break
                except OSError:
                    time.sleep(2)
            for leftover in [f for f in os.listdir(os.path.dirname(old) or ".") if f.lower().startswith("pcbooster")
                             and f.lower().endswith(".old")] if os.path.isdir(os.path.dirname(old)) else []:
                try:
                    os.remove(os.path.join(os.path.dirname(old), leftover))
                except OSError:
                    pass
        # 2) attività e collegamenti che puntano a questo exe
        if s.get("no_uac_prompt", True):
            launcher.enable()
        fixed = launcher.repair_shortcuts()
        if fixed:
            updater.log(f"Collegamenti riparati: {fixed}")
            tray_mod.refresh_icons()
        # 3) se l'exe ha cambiato posto, aggiorna anche le altre attività
        if s.get("registered_path") != sys.executable:
            try:
                if s.get("start_with_windows"):
                    tray_mod.set_start_with_windows(True)
                freq = api.get_auto_clean().get("freq", "off")
                if freq != "off":
                    api.set_auto_clean(freq)
                updater.save_settings({"registered_path": sys.executable})
            except Exception as e:
                updater.log(f"Aggiornamento attività non riuscito: {e}")
    if IS_WINDOWS and IS_FROZEN:
        threading.Thread(target=run, daemon=True).start()
