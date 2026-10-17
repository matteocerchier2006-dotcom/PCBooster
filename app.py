"""
PC Booster - avvio dell'applicazione.
Apre una finestra con l'interfaccia (cartella ui/) collegata al motore (backend.py).
"""
import os
import sys
import threading

import webview

import updater
from backend import Api, is_admin, relaunch_as_admin, IS_WINDOWS


def resource_path(rel: str) -> str:
    """Trova i file sia da sorgente sia dentro l'.exe di PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class AppApi(Api):
    # ---------- aggiornamento dell'app ----------
    def get_app_info(self):
        s = updater.load_settings()
        return {"version": updater.app_version(), "auto_update": s.get("auto_update", True),
                "configured": bool(updater.GITHUB_REPO), "repo": updater.GITHUB_REPO,
                "frozen": updater.IS_FROZEN}

    def set_auto_update(self, enabled):
        updater.save_settings({"auto_update": bool(enabled)})
        return {"ok": True, "msg": "Aggiornamento automatico " + ("attivato." if enabled else "disattivato.")}

    def check_app_update(self):
        return updater.check_for_update()

    def install_app_update(self, url, size=0):
        r = updater.install_update(url, size)
        if r["ok"]:
            threading.Timer(1.5, lambda: [w.destroy() for w in list(webview.windows)]).start()
        return r

    def restart_as_admin(self):
        if relaunch_as_admin():
            for w in webview.windows:
                w.destroy()
            return True
        return False


def main():
    # Chiede subito i permessi di amministratore (servono per driver, aggiornamenti e pulizia di sistema)
    if IS_WINDOWS and not is_admin() and "--no-admin" not in sys.argv:
        if relaunch_as_admin():
            sys.exit(0)
        # se l'utente ha rifiutato la finestra UAC, l'app parte comunque in modalità limitata

    webview.create_window(
        "PC Booster",
        resource_path(os.path.join("ui", "index.html")),
        js_api=AppApi(),
        width=1180, height=760, min_size=(980, 640),
        background_color="#0b0d12",
    )
    webview.start()


if __name__ == "__main__":
    main()
