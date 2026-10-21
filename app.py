"""
PC Booster - avvio dell'applicazione.
Apre una finestra con l'interfaccia (cartella ui/) collegata al motore (backend.py).

Avvii speciali:
  --auto-clean   pulizia programmata, senza finestra (la usa l'Utilità di pianificazione)
  --no-admin     non chiede i permessi di amministratore
"""
import os
import re
import sys
import threading

import updater
import launcher
import apptools
import tray
import installer
import history
import diagnostics
import i18n
from backend import Api, is_admin, relaunch_as_admin, harden_api, IS_WINDOWS

ALLOWED_PREFS = set(updater.DEFAULT_SETTINGS) - {"game_mode_state", "game_mode_opts", "game_mode_close", "last_version",
                                                  "notified", "tray_tip_seen", "start_with_windows", "clean_choice"}


def resource_path(rel: str) -> str:
    """Trova i file sia da sorgente sia dentro l'.exe di PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class AppApi(Api):
    def __init__(self):
        super().__init__()
        self._update = {"status": "idle"}   # idle | checking | none | ready | error
        self._update_lock = threading.Lock()
        # Mostra "Aggiornato alla versione X" la prima volta dopo un aggiornamento
        last = updater.load_settings().get("last_version", "")
        now = updater.app_version()
        self._just_updated = bool(last) and last != now
        if last != now:
            updater.save_settings({"last_version": now})

    # ---------- preferenze (aspetto, opzioni) ----------
    def get_prefs(self):
        s = updater.load_settings()
        s["sys_lang"] = "it" if i18n.windows_is_italian() else "en"    # per la lingua "automatica"
        return s

    def set_prefs(self, data):
        """Salva solo preferenze conosciute e del tipo giusto (l'interfaccia non può scrivere altro)."""
        clean = {}
        for k, v in (data or {}).items() if isinstance(data, dict) else []:
            if k not in ALLOWED_PREFS:
                continue
            default = updater.DEFAULT_SETTINGS.get(k)
            if isinstance(default, bool):
                if isinstance(v, bool):
                    clean[k] = v
            elif isinstance(default, str):
                if isinstance(v, str) and len(v) <= 40 and re.fullmatch(r"[\w#\-. ]*", v):
                    if k != "accent" or re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                        clean[k] = v
        return updater.save_settings(clean)

    APPEARANCE = ("theme", "accent", "font", "font_size", "radius", "compact_sidebar", "animations", "glow")

    def reset_prefs(self):
        """Rimette l'aspetto predefinito (solo l'aspetto: il resto delle impostazioni resta com'è)."""
        return updater.save_settings({k: updater.DEFAULT_SETTINGS[k] for k in self.APPEARANCE})

    # ---------- aggiornamento dell'app ----------
    def get_app_info(self):
        return {"version": updater.app_version(), "configured": bool(updater.GITHUB_REPO),
                "just_updated": self._just_updated,
                "repo": updater.GITHUB_REPO, "frozen": updater.IS_FROZEN, "update": self._public_update()}

    def _public_update(self):
        return {k: v for k, v in self._update.items() if k != "path"}

    def prepare_update(self):
        """Controlla GitHub e, se c'è una versione nuova, la scarica in silenzio."""
        with self._update_lock:
            if self._update["status"] in ("checking", "ready"):
                return self._public_update()
            self._update = {"status": "checking"}
        r = updater.check_for_update()
        if not r["ok"]:
            self._update = {"status": "error", "msg": r["msg"]}
        elif not r["available"]:
            self._update = {"status": "none", "msg": r["msg"], "latest": r.get("latest")}
        elif not updater.IS_FROZEN:
            self._update = {"status": "source", "latest": r["latest"], "notes": r.get("notes", ""),
                            "msg": f"C'è la versione {r['latest']}: scaricala da GitHub."}
        else:
            d = updater.download_update(r["url"], r.get("size", 0), r.get("sha256", ""))
            if d["ok"]:
                self._update = {"status": "ready", "latest": r["latest"], "notes": r.get("notes", ""),
                                "path": d["path"], "msg": f"Versione {r['latest']} pronta."}
            else:
                self._update = {"status": "error", "msg": d["msg"]}
        return self._public_update()

    def apply_update_now(self):
        if self._update.get("status") != "ready":
            return {"ok": False, "msg": "Nessun aggiornamento pronto."}
        r = updater.apply_update(self._update["path"], restart=True)
        if r["ok"]:
            self._update["status"] = "applied"
            import webview
            threading.Timer(1.0, lambda: [w.destroy() for w in list(webview.windows)]).start()
        return r

    def on_close(self):
        """Alla chiusura: se un aggiornamento è pronto, lo installa (la prossima apertura è già aggiornata)."""
        if self._update.get("status") == "ready" and updater.load_settings().get("auto_update", True):
            try:
                updater.apply_update(self._update["path"], restart=False)
            except Exception as e:
                diagnostics.log_error(f"Aggiornamento alla chiusura non riuscito: {e}")
            self._update["status"] = "applied"

    # ---------- modalità partita ----------
    def get_game_mode(self):
        s = updater.load_settings()
        state = s.get("game_mode_state")
        apps = [] if state else apptools.running_apps()
        remember = s.get("game_mode_close")
        if remember is not None:
            for a in apps:
                a["suggest"] = a["exe"] in remember
        return {"state": state, "opts": s.get("game_mode_opts"), "apps": apps}

    def start_game_mode(self, opts, close_names):
        opts = {k: bool(v) for k, v in (opts or {}).items() if k in ("power", "ram", "close", "reopen")}
        names = [n for n in (close_names or []) if isinstance(n, str)]
        state = apptools.start_game_mode(opts, names)
        updater.save_settings({"game_mode_state": state, "game_mode_opts": opts, "game_mode_close": names})
        history.add("game", "Modalità partita avviata", detail=state.get("msg", ""))
        return state

    def stop_game_mode(self):
        s = updater.load_settings()
        state = s.get("game_mode_state") or {}
        r = apptools.stop_game_mode(state, (s.get("game_mode_opts") or {}).get("reopen", True))
        updater.save_settings({"game_mode_state": None})
        history.add("game", "Modalità partita terminata", detail=r.get("msg", ""))
        return r

    # ---------- icona vicino all'orologio ----------
    def set_tray(self, key, enabled):
        if key == "start_with_windows":
            r = tray.set_start_with_windows(bool(enabled))
            if r["ok"]:
                updater.save_settings({"start_with_windows": bool(enabled)})
            return r
        if key in ("tray_enabled", "notifications"):
            updater.save_settings({key: bool(enabled)})
            if key == "tray_enabled" and enabled and getattr(self, "_tray", None) and self._tray.icon is None:
                self._tray.start()
            msgs = {"tray_enabled": ("Chiudendo la finestra PC Booster resterà vicino all'orologio.",
                                     "Chiudendo la finestra PC Booster si chiuderà del tutto."),
                    "notifications": ("Notifiche attivate.", "Notifiche disattivate.")}[key]
            return {"ok": True, "msg": msgs[0] if enabled else msgs[1]}
        return {"ok": False, "msg": "Opzione sconosciuta."}

    def tray_check_now(self):
        if getattr(self, "_tray", None) and self._tray.icon:
            self._tray.check_now()
            return {"ok": True, "msg": "Controllo in corso: se trovo qualcosa ti avviso con una notifica."}
        return {"ok": False, "msg": "Attiva prima l'icona vicino all'orologio."}

    # ---------- avvio senza richiesta dei permessi ----------
    def set_no_uac(self, enabled):
        updater.save_settings({"no_uac_prompt": bool(enabled)})
        return launcher.enable() if enabled else launcher.disable()

    def restart_as_admin(self):
        if relaunch_as_admin():
            import webview
            for w in list(webview.windows):
                w.destroy()
            return True
        return False


harden_api(AppApi)


def main():
    diagnostics.install_hooks()
    # Pulizia programmata: nessuna finestra, pulisce e chiude
    if "--auto-clean" in sys.argv:
        Api().auto_clean()
        return

    # Se PC Booster è già aperto (anche solo vicino all'orologio), mostra quello e basta
    if tray.signal_existing_instance():
        return

    # Servono i permessi di amministratore (driver, aggiornamenti, pulizia di sistema):
    # se c'è l'attività "PC Booster - Avvio" li prende da lì senza chiedere, altrimenti chiede a Windows
    if IS_WINDOWS and not is_admin() and "--no-admin" not in sys.argv:
        if launcher.IS_FROZEN and launcher.task_exists() and launcher.run_task():
            return
        if relaunch_as_admin():
            return

    # Prima volta da Download/Documenti: si installa in C:\Program Files\PC Booster e si riapre da lì
    if is_admin() and installer.needs_install():
        if installer.install_and_launch():
            return

    import webview
    updater.cleanup_old_versions()
    api = AppApi()
    prefs = updater.load_settings()

    # Parte nascosto se avviato con Windows (--tray) o dopo un aggiornamento fatto in background
    flag = os.path.join(os.path.dirname(updater._settings_path()), "start_hidden")
    start_hidden = "--tray" in sys.argv or os.path.exists(flag)
    if os.path.exists(flag):
        try:
            os.remove(flag)
        except OSError:
            pass

    # Tiene aggiornati attività e collegamenti, e cancella la copia vecchia dopo l'installazione
    if is_admin():
        installer.post_start_maintenance(api, tray)
    if api._just_updated:
        tray.refresh_icons()

    bg = {"light": "#eef0f5", "amoled": "#000000"}.get(prefs.get("theme"), "#111319")
    window = webview.create_window(
        "PC Booster",
        resource_path(os.path.join("ui", "index.html")),
        js_api=api,
        width=1220, height=780, min_size=(1000, 660),
        background_color=bg, hidden=start_hidden,
    )
    icon = tray.Tray(api, window, resource_path("icon.ico"))
    icon._visible = not start_hidden
    api._tray = icon
    window.events.closing += icon.on_closing
    window.events.closed += icon.on_closed
    window.events.closed += api.on_close

    def on_start():
        tray.start_instance_server(icon.show_window)
        if prefs.get("tray_enabled", True) or start_hidden:
            if not icon.start() and start_hidden:
                icon.show_window()   # senza icona non si può restare nascosti

    # icona della finestra e della barra delle applicazioni (tutte le misure, non solo quella piccola)
    webview.start(on_start, icon=resource_path("icon.ico"))


if __name__ == "__main__":
    main()
