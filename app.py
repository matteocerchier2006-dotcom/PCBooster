"""
PC Booster - avvio dell'applicazione.
Apre una finestra con l'interfaccia (cartella ui/) collegata al motore (backend.py).

Avvii speciali:
  --auto-clean   pulizia programmata, senza finestra (la usa l'Utilità di pianificazione)
  --no-admin     non chiede i permessi di amministratore
"""
import os
import sys
import threading

import updater
import launcher
import apptools
from backend import Api, is_admin, relaunch_as_admin, IS_WINDOWS

ALLOWED_PREFS = set(updater.DEFAULT_SETTINGS) - {"game_mode_state", "game_mode_opts", "game_mode_close"}


def resource_path(rel: str) -> str:
    """Trova i file sia da sorgente sia dentro l'.exe di PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class AppApi(Api):
    def __init__(self):
        super().__init__()
        self._update = {"status": "idle"}   # idle | checking | none | ready | error
        self._update_lock = threading.Lock()

    # ---------- preferenze (aspetto, opzioni) ----------
    def get_prefs(self):
        return updater.load_settings()

    def set_prefs(self, data):
        clean = {k: v for k, v in (data or {}).items() if k in ALLOWED_PREFS}
        return updater.save_settings(clean)

    def reset_prefs(self):
        keep = {"auto_update": updater.load_settings().get("auto_update", True)}
        defaults = dict(updater.DEFAULT_SETTINGS)
        defaults.update(keep)
        return updater.save_settings(defaults)

    # ---------- aggiornamento dell'app ----------
    def get_app_info(self):
        return {"version": updater.app_version(), "configured": bool(updater.GITHUB_REPO),
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
            d = updater.download_update(r["url"], r.get("size", 0))
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
        """Alla chiusura: se un aggiornamento è pronto, lo installa (senza riaprire l'app)."""
        if self._update.get("status") == "ready" and updater.load_settings().get("auto_update", True):
            updater.apply_update(self._update["path"], restart=False)

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
        return state

    def stop_game_mode(self):
        s = updater.load_settings()
        state = s.get("game_mode_state") or {}
        r = apptools.stop_game_mode(state, (s.get("game_mode_opts") or {}).get("reopen", True))
        updater.save_settings({"game_mode_state": None})
        return r

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


def main():
    # Pulizia programmata: nessuna finestra, pulisce e chiude
    if "--auto-clean" in sys.argv:
        Api().auto_clean()
        return

    # Chiede i permessi di amministratore (servono per driver, aggiornamenti e pulizia di sistema)
    if IS_WINDOWS and not is_admin() and "--no-admin" not in sys.argv:
        if relaunch_as_admin():
            sys.exit(0)

    import webview
    api = AppApi()
    prefs = updater.load_settings()
    # Tiene aggiornati attività e collegamento (anche se l'exe è stato spostato)
    if IS_WINDOWS and launcher.IS_FROZEN and is_admin() and prefs.get("no_uac_prompt", True):
        threading.Thread(target=launcher.enable, daemon=True).start()
    bg = {"light": "#eef0f5", "amoled": "#000000"}.get(prefs.get("theme"), "#111319")
    window = webview.create_window(
        "PC Booster",
        resource_path(os.path.join("ui", "index.html")),
        js_api=api,
        width=1220, height=780, min_size=(1000, 660),
        background_color=bg,
    )
    window.events.closed += api.on_close
    webview.start()


if __name__ == "__main__":
    main()
