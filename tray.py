"""
PC Booster - icona vicino all'orologio, una sola istanza e controlli in background.

- Chiudendo la finestra, PC Booster resta attivo vicino all'orologio (si può disattivare).
- Se lo riapri mentre è già attivo, mostra la finestra esistente invece di aprirne un'altra.
- Ogni qualche ora controlla: aggiornamenti dell'app, nuovo driver video, file da pulire.
  Se trova qualcosa manda una notifica di Windows.
"""
import os
import sys
import time
import socket
import threading
import subprocess

import updater

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


def _data_dir():
    return os.path.dirname(updater._settings_path())


# ======================================================================
# Una sola istanza: la seconda chiede alla prima di mostrarsi e si chiude
# ======================================================================
def _port_file():
    return os.path.join(_data_dir(), "instance.port")


def signal_existing_instance():
    try:
        with open(_port_file(), encoding="utf-8") as f:
            port = int(f.read().strip())
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as s:
            s.sendall(b"show")
            return s.recv(16) == b"ok"
    except (OSError, ValueError):
        return False


def start_instance_server(on_show):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    with open(_port_file(), "w", encoding="utf-8") as f:
        f.write(str(srv.getsockname()[1]))

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    if conn.recv(16) == b"show":
                        conn.sendall(b"ok")
                        on_show()
            except OSError:
                time.sleep(1)
    threading.Thread(target=loop, daemon=True).start()


# ======================================================================
# Avvio insieme a Windows (attività con i permessi già concessi)
# ======================================================================
LOGON_TASK = "PC Booster - Accensione"


def set_start_with_windows(enabled):
    if not IS_WINDOWS:
        return {"ok": True, "msg": "Demo: avvio con Windows " + ("attivato." if enabled else "disattivato.")}
    if not enabled:
        subprocess.run(["schtasks", "/Delete", "/TN", LOGON_TASK, "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
        return {"ok": True, "msg": "PC Booster non partirà più con Windows."}
    if not getattr(sys, "frozen", False):
        return {"ok": False, "msg": "Disponibile solo nella versione .exe."}
    exe = sys.executable.replace("'", "''")
    folder = os.path.dirname(sys.executable).replace("'", "''")
    script = (
        f"$a=New-ScheduledTaskAction -Execute '{exe}' -Argument '--tray' -WorkingDirectory '{folder}';"
        "$t=New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"; $t.Delay='PT20S';"
        "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" -LogonType Interactive -RunLevel Highest;"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit ([TimeSpan]::Zero);"
        f"Register-ScheduledTask -TaskName '{LOGON_TASK}' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null; 'OK'"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
    if (r.stdout or "").strip().endswith("OK"):
        return {"ok": True, "msg": "PC Booster partirà con Windows, già vicino all'orologio."}
    return {"ok": False, "msg": "Non sono riuscito ad attivarlo: servono i permessi di amministratore."}


def refresh_icons():
    """Fa vedere a Windows la nuova icona (sul desktop e nella barra) dopo un aggiornamento."""
    if IS_WINDOWS:
        try:
            subprocess.Popen(["ie4uinit.exe", "-show"], creationflags=CREATE_NO_WINDOW)
        except OSError:
            pass


# ======================================================================
# Icona vicino all'orologio
# ======================================================================
class Tray:
    def __init__(self, api, window, icon_path):
        self.api = api
        self.window = window
        self.icon_path = icon_path
        self.icon = None
        self.quitting = False
        self.hidden_tip_shown = False

    # ---------- finestra ----------
    def show_window(self):
        try:
            self.window.show()
            self.window.restore()
            self._visible = True
        except Exception:
            pass

    def on_closed(self):
        """La finestra si è chiusa davvero: toglie anche l'icona, così il programma termina."""
        self.quitting = True
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass

    def on_closing(self):
        """Premendo la X: nasconde la finestra invece di chiudere (se l'opzione è attiva)."""
        if self.quitting or not updater.load_settings().get("tray_enabled", True) or self.icon is None:
            return True
        self.window.hide()
        self._visible = False
        if not self.hidden_tip_shown and not updater.load_settings().get("tray_tip_seen"):
            self.notify("PC Booster è ancora attivo",
                        "Lo trovi qui vicino all'orologio. Per chiuderlo del tutto: tasto destro → Esci.")
            updater.save_settings({"tray_tip_seen": True})
        self.hidden_tip_shown = True
        return False

    def quit(self, *_):
        self.quitting = True
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass

    # ---------- notifiche ----------
    def notify(self, title, message):
        if self.icon is None or not updater.load_settings().get("notifications", True):
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    # ---------- azioni del menu ----------
    def _game_active(self):
        return bool(updater.load_settings().get("game_mode_state"))

    def toggle_game(self, *_):
        def run():
            if self._game_active():
                r = self.api.stop_game_mode()
                self.notify("Modalità partita terminata", r["msg"])
            else:
                s = updater.load_settings()
                names = s.get("game_mode_close")
                if names is None:
                    import apptools
                    names = [a["exe"] for a in apptools.running_apps() if a["suggest"]]
                st = self.api.start_game_mode(s.get("game_mode_opts") or {}, names)
                self.notify("Modalità partita attiva", st["msg"])
            self.icon.update_menu()
            self._refresh_page()
        threading.Thread(target=run, daemon=True).start()

    def quick_clean(self, *_):
        def run():
            self.notify("Pulizia veloce", "Sto pulendo i file inutili…")
            r = self.api.auto_clean()
            self.notify("Pulizia completata", r["msg"])
        threading.Thread(target=run, daemon=True).start()

    def check_now(self, *_):
        threading.Thread(target=lambda: self.background_check(manual=True), daemon=True).start()

    def _refresh_page(self):
        try:
            self.window.evaluate_js("window.onTrayChange && window.onTrayChange()")
        except Exception:
            pass

    # ---------- avvio ----------
    def start(self):
        try:
            import pystray
            from PIL import Image
        except Exception as e:
            updater.log(f"Icona vicino all'orologio non disponibile: {e}")
            return False
        image = Image.open(self.icon_path)
        M = pystray.MenuItem
        menu = pystray.Menu(
            M("Apri PC Booster", lambda *_: self.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            M(lambda item: "Termina modalità partita" if self._game_active() else "Avvia modalità partita",
              self.toggle_game),
            M("Pulizia veloce", self.quick_clean),
            M("Controlla adesso", self.check_now),
            pystray.Menu.SEPARATOR,
            M("Esci", self.quit),
        )
        self.icon = pystray.Icon("PCBooster", image, "PC Booster", menu)
        self.icon.run_detached()
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    # ---------- controlli in background ----------
    def _loop(self):
        time.sleep(120)
        while not self.quitting:
            try:
                self.background_check()
            except Exception as e:
                updater.log(f"Controllo in background non riuscito: {e}")
            time.sleep(6 * 3600)

    def background_check(self, manual=False):
        s = updater.load_settings()
        seen = dict(s.get("notified") or {})
        found = False

        # 1) aggiornamento di PC Booster: se la finestra è nascosta lo installa e si riapre nascosto
        if s.get("auto_update", True):
            u = self.api.prepare_update()
            if u.get("status") == "ready":
                found = True
                if not self.window_visible() and not s.get("game_mode_state"):
                    with open(os.path.join(_data_dir(), "start_hidden"), "w") as f:
                        f.write("1")
                    r = self.api.apply_update_now()
                    if r.get("ok"):
                        return
                elif seen.get("app") != u.get("latest"):
                    self.notify("Aggiornamento di PC Booster pronto",
                                f"La versione {u.get('latest')} si installa quando chiudi l'app.")
                    seen["app"] = u.get("latest")

        # 2) nuovo driver della scheda video
        try:
            gpus = self.api.get_gpu_info()
            main = next((g for g in gpus if g["vendor"] in ("nvidia", "amd")
                         and (g["kind"] == "Dedicata" or len(gpus) == 1)), None)
            if main:
                d = self.api.check_gpu_driver(main["name"])
                if d.get("ok") and d.get("update"):
                    found = True
                    if manual or seen.get("driver") != d["version"]:
                        self.notify("Nuovo driver per la scheda video",
                                    f"{main['name']}: è uscito il {d['version']} (tu hai il {d['installed']}).")
                        seen["driver"] = d["version"]
        except Exception as e:
            updater.log(f"Controllo driver non riuscito: {e}")

        # 3) minacce trovate da Microsoft Defender
        try:
            active = [t for t in self.api.security_threats() if t.get("active")]
            if active and (manual or seen.get("threats") != len(active)):
                found = True
                self.notify("Attenzione: minacce trovate",
                            f"Microsoft Defender ha trovato {len(active)} minacce. Apri PC Booster → Sicurezza.")
            seen["threats"] = len(active)
        except Exception as e:
            updater.log(f"Controllo minacce non riuscito: {e}")

        # 4) file da pulire (al massimo un avviso al giorno)
        today = time.strftime("%Y-%m-%d")
        if manual or seen.get("junk_day") != today:
            scan = self.api.scan_junk()
            total = sum(i["bytes"] or 0 for i in scan["items"] if i["default"])
            if total > 2 * 1024 ** 3:
                found = True
                self.notify("C'è spazio da liberare",
                            f"{total / 1024 ** 3:.1f} GB di file inutili. Tasto destro sull'icona → Pulizia veloce.")
                seen["junk_day"] = today

        if manual and not found:
            self.notify("Tutto a posto", "Nessun aggiornamento, driver nuovo o file da pulire.")
        updater.save_settings({"notified": seen})

    def window_visible(self):
        return self._visible

    _visible = True
