"""
PC Booster - comandi di Windows fatti bene, in un posto solo.

Tutte le parti dell'app lanciano PowerShell, schtasks, powercfg, winget... da qui, così:
- i programmi di sistema si prendono sempre da C:\\Windows\\System32 (mai una copia con lo
  stesso nome lasciata nella cartella da cui parte l'app: PC Booster gira da amministratore);
- non si apre nessuna finestra nera e niente può restare in attesa di una risposta da tastiera;
- c'è sempre un tempo massimo: un comando bloccato non blocca l'app;
- il testo con le lettere accentate si legge bene (UTF-8 o la codifica della console);
- gli errori finiscono in errori.log invece di far crashare l'app.
"""
import os
import sys
import json
import time
import threading
import subprocess

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

_SYSROOT = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
_SYS32 = os.path.join(_SYSROOT, "System32")
_SYSTEM_TOOLS = {
    "powershell": os.path.join(_SYS32, "WindowsPowerShell", "v1.0", "powershell.exe"),
    "schtasks": os.path.join(_SYS32, "schtasks.exe"),
    "powercfg": os.path.join(_SYS32, "powercfg.exe"),
    "ipconfig": os.path.join(_SYS32, "ipconfig.exe"),
    "netsh": os.path.join(_SYS32, "netsh.exe"),
    "taskkill": os.path.join(_SYS32, "taskkill.exe"),
    "cmd": os.path.join(_SYS32, "cmd.exe"),
    "dism": os.path.join(_SYS32, "Dism.exe"),
    "takeown": os.path.join(_SYS32, "takeown.exe"),
    "icacls": os.path.join(_SYS32, "icacls.exe"),
    "explorer": os.path.join(_SYSROOT, "explorer.exe"),
    "rstrui": os.path.join(_SYS32, "rstrui.exe"),
    "ie4uinit": os.path.join(_SYS32, "ie4uinit.exe"),
}


def tool(name):
    """Percorso completo di un programma di Windows (es. tool('schtasks'))."""
    key = name.lower()
    if key.endswith(".exe"):
        key = key[:-4]
    path = _SYSTEM_TOOLS.get(key)
    if path and (not IS_WINDOWS or os.path.isfile(path)):
        return path
    return name


def _log(text):
    try:
        import diagnostics
        diagnostics.log_error(text)
    except Exception:
        pass


def decode(raw):
    """Testo da un comando di Windows: prova UTF-8, poi la codifica della console (OEM)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("oem" if IS_WINDOWS else "latin-1")
        except (UnicodeDecodeError, LookupError):
            return raw.decode("latin-1", "replace")


class Result:
    """Esito di un comando. rc = -1 se non è partito o se ha superato il tempo massimo."""
    __slots__ = ("rc", "out", "err", "timed_out", "error")

    def __init__(self, rc=-1, out="", err="", timed_out=False, error=""):
        self.rc, self.out, self.err, self.timed_out, self.error = rc, out, err, timed_out, error

    @property
    def ok(self):
        return self.rc == 0

    def __repr__(self):
        return f"Result(rc={self.rc}, timed_out={self.timed_out}, out={self.out[:80]!r})"


def _kill_tree(p):
    """Chiude il programma E tutto quello che ha avviato (es. winget -> installazione del produttore).
    Python da solo chiude solo il primo, gli altri resterebbero aperti in background."""
    if IS_WINDOWS:
        try:
            subprocess.run([tool("taskkill"), "/T", "/F", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        p.kill()
    except OSError:
        pass


def run(args, timeout=60, env=None, cwd=None, log_errors=True, stop_on_timeout=True):
    """Esegue un programma e aspetta la fine. Non solleva mai eccezioni: guarda Result.ok.

    Se supera il tempo massimo:
    - di norma viene chiuso insieme a tutti i programmi che ha avviato;
    - con stop_on_timeout=False viene lasciato finire da solo (per operazioni di Windows come DISM,
      che non vanno mai interrotte a metà). In quel caso il suo testo non viene letto."""
    args = [str(a) for a in args]
    if args:
        args[0] = tool(args[0])
    out_pipe = subprocess.PIPE if stop_on_timeout else subprocess.DEVNULL
    try:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out_pipe, stderr=out_pipe, env=env, cwd=cwd,
                             creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
    except OSError as e:
        if log_errors:
            _log(f"Comando non avviato: {' '.join(args)[:300]} -> {e}")
        return Result(-1, error=str(e))
    try:
        out, err = p.communicate(timeout=timeout)
        return Result(p.returncode, decode(out), decode(err))
    except subprocess.TimeoutExpired:
        if log_errors:
            _log(f"Tempo scaduto ({timeout}s){'' if stop_on_timeout else ', lasciato finire'}: {' '.join(args)[:300]}")
        if not stop_on_timeout:
            return Result(-1, timed_out=True, error="timeout")
        _kill_tree(p)
        try:
            out, err = p.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            out, err = b"", b""
        return Result(-1, decode(out), decode(err), timed_out=True, error="timeout")


def start(args, cwd=None, hidden=True):
    """Avvia un programma senza aspettarlo. True se è partito."""
    args = [str(a) for a in args]
    if args:
        args[0] = tool(args[0])
    try:
        subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, close_fds=True,
                         creationflags=CREATE_NO_WINDOW if (IS_WINDOWS and hidden) else 0)
        return True
    except OSError as e:
        _log(f"Avvio non riuscito: {' '.join(args)[:300]} -> {e}")
        return False


# ---------------------------------------------------------------- PowerShell
_PS_PREFIX = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "$ProgressPreference='SilentlyContinue';$ConfirmPreference='None';")


def ps_run(script, timeout=120):
    """Esegue uno script PowerShell senza finestra e senza domande. Restituisce Result."""
    if not IS_WINDOWS:
        return Result(0, "")
    r = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _PS_PREFIX + script], timeout=timeout)
    r.out = (r.out or "").strip()
    return r


def ps(script, timeout=120, raise_timeout=False):
    """Come ps_run ma restituisce solo il testo ("" se qualcosa va storto)."""
    r = ps_run(script, timeout)
    if r.timed_out and raise_timeout:
        raise subprocess.TimeoutExpired("powershell", timeout)
    return r.out


def ps_json(script, timeout=120, raise_timeout=False):
    """Esegue lo script e legge il risultato come JSON. None se non c'è o non è valido."""
    out = ps(script + " | ConvertTo-Json -Depth 5 -Compress", timeout, raise_timeout)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        # a volte prima del JSON c'è un avviso: si prova a partire dalla prima parentesi
        for i, c in enumerate(out):
            if c in "[{":
                try:
                    return json.loads(out[i:])
                except ValueError:
                    break
        _log(f"PowerShell: risposta non leggibile: {out[:300]}")
        return None


def as_list(x):
    return [] if x is None else x if isinstance(x, list) else [x]


def q(s):
    """Testo sicuro dentro 'apici' di PowerShell."""
    return str(s).replace("'", "''")


# ---------------------------------------------------------------- file
_write_lock = threading.Lock()


def atomic_write(path, text, encoding="utf-8"):
    """Scrive un file in modo sicuro: prima una copia, poi la sostituzione in un colpo solo.
    Se il PC si spegne a metà, resta il file vecchio intero invece di uno rovinato."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # su Windows un antivirus o un altro thread può tenere il file aperto per un attimo
            time.sleep(0.1 * (attempt + 1))
    try:
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
