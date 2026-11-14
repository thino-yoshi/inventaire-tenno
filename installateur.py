"""Petit installateur de Lotus Desk (Installer-LotusDesk.exe).

Leger a envoyer (Discord...) : il ne contient pas l'app, il telecharge la derniere version publiee
sur GitHub, la range dans son propre dossier, cree les raccourcis (Bureau + menu Demarrer) et la lance.
Ensuite, l'app se met a jour toute seule, comme d'habitude.

Dossier : %LOCALAPPDATA%\\Programs\\Lotus Desk (pas besoin des droits administrateur, et hors de
OneDrive, qui synchronise mal le cache de la fenetre).
Telechargement par curl.exe, fourni avec Windows 10/11 : Python n'embarque alors pas sa propre
bibliotheque de chiffrement (la plus lourde), et l'installateur reste sous la limite de Discord.
"""
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = "thino-yoshi/inventaire-tenno"
EXE = "LotusDesk.exe"
TITRE = "Installer Lotus Desk"
DOSSIER = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / "Lotus Desk"
NO_WINDOW = 0x08000000

MB_OK, MB_OKCANCEL, MB_ICONINFO, MB_ICONERROR, IDOK = 0x0, 0x1, 0x40, 0x10, 1


def boite(texte, style=MB_OK | MB_ICONINFO):
    return ctypes.windll.user32.MessageBoxW(None, texte, TITRE, style)


def curl(*args, timeout=120):
    """curl.exe de Windows (connexion securisee geree par Windows)."""
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    r = subprocess.run([str(exe) if exe.exists() else "curl", "-fsSL", "--retry", "2",
                        "-A", "LotusDesk-installateur", *args],
                       capture_output=True, creationflags=NO_WINDOW, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"curl {r.returncode} : {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def lien_derniere_version():
    rel = json.loads(curl("-H", "Accept: application/vnd.github+json",
                          f"https://api.github.com/repos/{REPO}/releases/latest", timeout=30))
    for a in rel.get("assets", []):
        if a.get("name") == EXE:
            url = a["browser_download_url"]
            if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
                raise RuntimeError("source de telechargement inattendue")
            return url, rel.get("tag_name", "")
    raise RuntimeError("LotusDesk.exe introuvable dans la derniere version")


def telecharger(url, dest):
    tmp = dest.with_name(dest.name + ".part")
    try:
        curl("-o", str(tmp), url, timeout=300)
        if tmp.stat().st_size < 1_000_000:  # page d'erreur au lieu de l'exe
            raise RuntimeError("fichier telecharge incomplet")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def raccourcis(exe):
    """Raccourcis Bureau + menu Demarrer (le Bureau peut etre dans OneDrive : on demande a Windows)."""
    q = lambda p: str(p).replace("'", "''")  # chemin entre apostrophes PowerShell (ex. nom O'Brien)
    ps = (
        "$s = New-Object -ComObject WScript.Shell;"
        "foreach ($d in @($s.SpecialFolders('Desktop'), $s.SpecialFolders('Programs'))) {"
        "  $l = $s.CreateShortcut((Join-Path $d 'Lotus Desk.lnk'));"
        f"  $l.TargetPath = '{q(exe)}'; $l.WorkingDirectory = '{q(exe.parent)}';"
        f"  $l.IconLocation = '{q(exe)},0'; $l.Description = 'Lotus Desk'; $l.Save() }}"
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   creationflags=NO_WINDOW, timeout=60, check=False)


def main():
    exe = DOSSIER / EXE
    deja = exe.exists()
    if not deja:
        if boite("Lotus Desk va être installé dans :\n\n"
                 f"{DOSSIER}\n\n"
                 "L'installateur télécharge la dernière version (environ 30 Mo), crée un raccourci "
                 "sur le Bureau et dans le menu Démarrer, puis lance l'application.\n\n"
                 "Cliquez sur OK, puis patientez quelques secondes.",
                 MB_OKCANCEL | MB_ICONINFO) != IDOK:
            return
        try:
            url, version = lien_derniere_version()
            DOSSIER.mkdir(parents=True, exist_ok=True)
            telecharger(url, exe)
        except Exception as e:
            boite("Le téléchargement a échoué. Vérifiez votre connexion Internet et réessayez.\n\n"
                  f"Détail : {e}", MB_OK | MB_ICONERROR)
            return
    try:
        raccourcis(exe)
    except Exception:
        pass  # pas grave : l'app est installee, seuls les raccourcis manquent
    subprocess.Popen([str(exe)], cwd=str(DOSSIER))
    if deja:
        boite("Lotus Desk était déjà installé : les raccourcis ont été recréés et l'application se lance.\n"
              "Elle se met à jour toute seule.")
    else:
        boite(f"Lotus Desk {version} est installé ! ♥\n\n"
              "Un raccourci « Lotus Desk » est sur votre Bureau et dans le menu Démarrer.\n"
              "L'application se met ensuite à jour toute seule.")


if __name__ == "__main__":
    if sys.platform == "win32":
        main()
