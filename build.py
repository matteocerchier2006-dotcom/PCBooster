"""
PC Booster - crea PCBooster.exe (lo usa GitHub quando pubblichi una release).

Uso:  python build.py v1.2.0 nomeutente/PCBooster
Scrive version.txt e repo.txt, prepara le informazioni del file (Proprietà → Dettagli
in Windows) e crea l'exe con l'icona.
"""
import os
import re
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def version_numbers(ver):
    nums = [int(x) for x in re.findall(r"\d+", ver)][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def version_file(ver):
    n = version_numbers(ver)
    return f"""# Informazioni mostrate da Windows in Proprietà → Dettagli
VSVersionInfo(
  ffi=FixedFileInfo(filevers={n}, prodvers={n}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041004B0', [
      StringStruct('CompanyName', 'PC Booster'),
      StringStruct('FileDescription', 'PC Booster'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'PCBooster'),
      StringStruct('OriginalFilename', 'PCBooster.exe'),
      StringStruct('ProductName', 'PC Booster'),
      StringStruct('ProductVersion', '{ver}'),
      StringStruct('LegalCopyright', 'PC Booster')])]),
    VarFileInfo([VarStruct('Translation', [0x0410, 1200])])
  ]
)
"""


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v0.0.0"
    repo = sys.argv[2] if len(sys.argv) > 2 else ""
    ver = tag.lstrip("vV")
    os.chdir(HERE)
    with open("version.txt", "w", encoding="utf-8") as f:
        f.write(ver)
    with open("repo.txt", "w", encoding="utf-8") as f:
        f.write(repo)
    with open("version_info.txt", "w", encoding="utf-8") as f:
        f.write(version_file(ver))
    sep = os.pathsep
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
           "--name", "PCBooster", "--icon", "icon.ico", "--version-file", "version_info.txt",
           "--add-data", f"ui{sep}ui", "--add-data", f"version.txt{sep}.", "--add-data", f"repo.txt{sep}.",
           "--add-data", f"icon.ico{sep}.", "--hidden-import", "pystray._win32", "app.py"]
    # Niente richiesta automatica dei permessi nel file: li gestisce l'app (vedi launcher.py),
    # così i collegamenti sulla barra delle applicazioni funzionano sempre.
    print("Versione:", ver, "| Repository:", repo)
    subprocess.check_call(cmd)


if __name__ == "__main__":
    main()
