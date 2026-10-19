"""
Overlay des recompenses de reliques (comme AlecaFrame / WFInfo).

1. Lit le journal du jeu (%LOCALAPPDATA%\\Warframe\\EE.log) en direct :
   - "ResourceLoader ... (/Lotus/Types/Game/Projections/...)" : reliques chargees par l'escouade
   - "ProjectionRewardChoice.lua: Relic rewards initialized" : l'ecran de choix s'ouvre (15 s)
   - "VoidProjections: ... gets reward /Lotus/StoreItems/..." : une des recompenses (souvent la sienne)
2. Capture l'ecran et lit les noms avec l'OCR integre a Windows (via PowerShell, rien a installer),
   puis les rapproche des recompenses possibles des reliques de l'escouade.
3. Affiche par-dessus le jeu une fenetre transparente et "traversable" a la souris avec le prix
   (minimum Warframe Market) et les ducats au-dessus de chaque recompense.

Le jeu doit etre en mode "plein ecran sans bordure" (un overlay ne peut pas passer au-dessus
du plein ecran exclusif).
"""
import ctypes
import difflib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "Warframe" / "EE.log"
RELIC_RE = re.compile(r"ResourceLoader .*\((/Lotus/Types/Game/Projections/[^)\s]+)\) Found")
GETS_RE = re.compile(r"VoidProjections: \S+ gets reward (/Lotus/\S+)")
OPEN_MARK = "ProjectionRewardChoice.lua: Relic rewards initialized"
SHOW_SECONDS = 16          # le choix dure 15 s
RELIC_MEMORY = 30 * 60     # reliques vues dans les 30 dernieres minutes = escouade actuelle

# OCR integre a Windows (Windows.Media.Ocr) appele depuis PowerShell : sortie JSON des lignes lues
OCR_PS = r"""
param($path, $lang)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(); $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($lang))
$res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
$out = @(foreach ($l in $res.Lines) {
  $r = $l.Words | ForEach-Object { $_.BoundingRect }
  $x = ($r | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
  $y = ($r | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
  $x2 = ($r | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
  $y2 = ($r | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
  [pscustomobject]@{ t = $l.Text; x = $x; y = $y; w = $x2 - $x; h = $y2 - $y } })
ConvertTo-Json -InputObject $out -Compress
"""


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(schema|blueprint)\b", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def ocr(image_path, lang="fr-FR"):
    """Lignes lues dans l'image : [{t, x, y, w, h}] (coordonnees de l'image)."""
    script = Path(tempfile.gettempdir()) / "inventaire_tenno_ocr.ps1"
    script.write_text(OCR_PS, encoding="utf-8-sig")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        os.path.abspath(image_path),  # l'OCR de Windows refuse les "/"
                        lang], capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (r.stdout or "").strip()
    return json.loads(out) if out.startswith("[") else []


def columns(lines, width, n=4):
    """Regroupe les lignes lues en n colonnes (une par carte de recompense)."""
    cols = [[] for _ in range(n)]
    for ln in lines:
        cx = ln["x"] + ln["w"] / 2
        cols[min(n - 1, max(0, int(cx / width * n)))].append(ln)
    return cols


class RewardOverlay:
    def __init__(self, app):
        self.app = app            # module app : game(), catalog(), prices(), relic tables...
        self.enabled = True
        self.relics = {}          # chemin de relique -> derniere fois vue
        self.known = []           # recompenses donnees par le journal pour l'ecran en cours
        self.window = None
        self.status = "en attente"
        self.last = None          # dernier resultat (pour l'interface)

    # ---------------------------------------------------------------- journal du jeu
    def follow_log(self):
        pos = LOG.stat().st_size if LOG.exists() else 0  # on ignore l'historique
        buf = b""
        while True:
            try:
                size = LOG.stat().st_size if LOG.exists() else 0
                if size < pos:  # le jeu a redemarre : nouveau journal
                    pos, buf = 0, b""
                if size > pos:
                    with open(LOG, "rb") as f:
                        f.seek(pos)
                        buf += f.read(size - pos)
                    pos = size
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        self.on_line(raw.decode("utf-8", "replace"))
            except Exception as e:
                self.status = f"journal illisible : {e}"
            time.sleep(0.25)

    def on_line(self, line):
        m = RELIC_RE.search(line)
        if m and m.group(1) in self.app.game().ex["relics"]:
            self.relics[m.group(1)] = time.time()
            return
        m = GETS_RE.search(line)
        if m:
            self.known.append(m.group(1).replace("/StoreItems/", "/", 1))
            return
        if OPEN_MARK in line and self.enabled:
            self.known = []
            threading.Timer(1.3, self.capture).start()  # le temps que les cartes s'affichent

    # ---------------------------------------------------------------- recompenses possibles
    def candidates(self, lang):
        """[(chemin, noms normalises)] : recompenses des reliques de l'escouade (ou de toutes)."""
        g = self.app.game()
        now = time.time()
        squad = [p for p, t in self.relics.items() if now - t < RELIC_MEMORY]
        relics = squad or list(g.ex["relics"])
        paths = set(self.known)
        for rp in relics:
            manifest = g.ex["relics"][rp].get("rewardManifest")
            for r in (g.rewards().get(manifest) or [[]])[0]:
                paths.add(r["type"].replace("/StoreItems/", "/", 1))
        out = []
        for p in paths:
            names = {norm(g.name(p, l)) for l in {lang, "en"} if g.name(p, l)}
            if names:
                out.append((p, names))
        return out, squad

    def match(self, text, cands):
        t = norm(text)
        if len(t) < 4:
            return None, 0
        best, score = None, 0
        for p, names in cands:
            for n in names:
                s = difflib.SequenceMatcher(None, t, n).ratio()
                if s > score:
                    best, score = p, s
        return best, score

    # ---------------------------------------------------------------- capture + OCR
    def capture(self):
        try:
            from PIL import ImageGrab
            lang = self.app.CURRENT_LANG[0]
            shot = ImageGrab.grab()
            W, H = shot.size
            top, bottom = int(H * 0.22), int(H * 0.62)  # bande des noms des cartes
            band = shot.crop((0, top, W, bottom))
            band = band.resize((band.width * 2, band.height * 2))  # texte plus gros = OCR plus fiable
            path = Path(tempfile.gettempdir()) / "inventaire_tenno_recompenses.png"
            band.save(path)
            self.status = "lecture de l'ecran"
            lines = ocr(path, "fr-FR" if lang == "fr" else "en-US")
            for ln in lines:  # retour en coordonnees ecran
                ln["x"], ln["w"] = ln["x"] / 2, ln["w"] / 2
                ln["y"], ln["h"] = ln["y"] / 2 + top, ln["h"] / 2
            self.show_rewards(lines, W, H, lang)
        except Exception as e:
            self.status = f"erreur : {e}"

    def show_rewards(self, lines, W, H, lang):
        cands, squad = self.candidates(lang)
        rewards = []
        for col in columns(lines, W):
            if not col:
                continue
            col.sort(key=lambda ln: ln["y"])
            text = " ".join(ln["t"] for ln in col)
            path, score = self.match(text, cands)
            # une carte peut contenir du texte parasite : on retente ligne par ligne
            for ln in col:
                p2, s2 = self.match(ln["t"], cands)
                if s2 > score:
                    path, score = p2, s2
            if path and score >= 0.6:
                x = min(ln["x"] for ln in col) + max(ln["x"] + ln["w"] for ln in col)
                rewards.append({"chemin": path, "x": x / 2, "y": min(ln["y"] for ln in col),
                                "score": round(score, 2), **self.value(path, lang)})
        best = max((r["prix"] or 0 for r in rewards), default=0)
        for r in rewards:
            r["meilleur"] = best > 0 and r["prix"] == best
        self.last = {"t": time.time(), "recompenses": rewards, "escouade": len(squad)}
        self.status = f"{len(rewards)} recompense(s) reconnue(s)"
        if rewards and self.window:
            self.window.show(rewards, SHOW_SECONDS)

    def value(self, path, lang):
        app = self.app
        g, cat = app.game(), app.catalog()
        en = g.name(path, "en")
        slug = cat.find(path, en)
        price = None
        if slug:
            try:
                price = app.prices(slug, cached_ok=True)["min"]
            except Exception:
                pass
        ducats = cat.by_slug.get(slug, {}).get("ducats") if slug else None
        return {"nom": g.name(path, lang) or en, "prix": price, "ducats": ducats}

    def start(self, assets=None):
        self.window = OverlayWindow(assets)
        threading.Thread(target=self.window.run, daemon=True).start()
        threading.Thread(target=self.follow_log, daemon=True).start()


# ================================================================ fenetre transparente
class OverlayWindow:
    """Fenetre plein ecran transparente, toujours au premier plan et traversable a la souris."""
    KEY = "#010203"  # couleur rendue transparente

    def __init__(self, assets=None):
        self.q = queue.Queue()
        self.assets = Path(assets) if assets else None  # icones platine / ducats

    def show(self, rewards, seconds):
        self.q.put(("show", rewards, seconds))

    def run(self):
        import tkinter as tk
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # memes pixels que la capture
        except Exception:
            pass
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", self.KEY)
        W, H = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+0+0")
        canvas = tk.Canvas(root, width=W, height=H, bg=self.KEY, highlightthickness=0)
        canvas.pack()
        state = {"hide_at": 0}
        icons = {}
        try:
            from PIL import Image, ImageTk
            for k in ("platine", "ducat"):
                im = Image.open(self.assets / f"{k}.png").convert("RGBA").resize((22, 22), Image.LANCZOS)
                icons[k] = ImageTk.PhotoImage(im, master=root)
        except Exception:
            pass  # sans icones : texte "pl" / "ducats"

        def click_through():
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            GWL_EXSTYLE = -20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # calque + transparent aux clics + pas dans la barre des taches + ne prend pas le focus
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                                style | 0x80000 | 0x20 | 0x80 | 0x8000000)

        def draw(rewards):
            canvas.delete("all")
            font = ("Segoe UI Semibold", 15)
            for r in rewards:
                gold = r.get("meilleur")
                # morceaux : [icone platine] 34   [icone ducats] 100
                parts = []
                if r["prix"] is not None:
                    parts.append(("platine", str(r["prix"]), "#0a0d14" if gold else "#7fd4e6"))
                if r["ducats"]:
                    parts.append(("ducat", str(r["ducats"]), "#0a0d14" if gold else "#c9a96e"))
                if not parts:
                    parts.append((None, "non vendable", "#8d8878"))
                y = r["y"] - 34
                ids, x = [], 0
                for icon, txt, color in parts:  # pose de gauche a droite, centre ensuite
                    if icon in icons:
                        ids.append(canvas.create_image(x, y, image=icons[icon], anchor="w"))
                        x += 26
                    elif icon:
                        txt += " pl" if icon == "platine" else " ducats"
                    t = canvas.create_text(x, y, text=txt, font=font, fill=color, anchor="w")
                    ids.append(t)
                    x = canvas.bbox(t)[2] + 16
                x1, y1, x2, y2 = canvas.bbox(*ids)
                dx = r["x"] - (x1 + x2) / 2
                for i in ids:
                    canvas.move(i, dx, 0)
                rect = canvas.create_rectangle(x1 + dx - 12, y1 - 6, x2 + dx + 12, y2 + 6,
                                               fill="#c9a96e" if gold else "#0b0f18",
                                               outline="#c9a96e", width=2)
                canvas.tag_lower(rect)

        def tick():
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "show":
                        draw(cmd[1])
                        root.deiconify()
                        root.attributes("-topmost", True)
                        click_through()
                        state["hide_at"] = time.time() + cmd[2]
            except queue.Empty:
                pass
            if state["hide_at"] and time.time() > state["hide_at"]:
                state["hide_at"] = 0
                canvas.delete("all")
                root.withdraw()
            root.after(100, tick)

        root.after(100, tick)
        root.mainloop()
