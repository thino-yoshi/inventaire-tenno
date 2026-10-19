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
# langue demandee absente (ex. Windows anglais sans pack francais) : langue(s) du compte Windows
if (-not $eng) { $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $eng) { [Console]::Error.WriteLine('OCR_INDISPONIBLE'); exit 2 }
[Console]::Error.WriteLine('OCR_LANG ' + $eng.RecognizerLanguage.LanguageTag)
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


def set_dpi_aware():
    """Coordonnees en vrais pixels partout (capture, position de la fenetre du jeu, overlay)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_game_rect(process="warframe.x64.exe"):
    """(gauche, haut, droite, bas) de la zone d'affichage de Warframe, sur n'importe quel ecran.
    None si le jeu n'est pas ouvert (on retombe alors sur l'ecran principal)."""
    import ctypes.wintypes as wt
    try:
        import psutil
        pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == process}
    except Exception:
        return None
    if not pids:
        return None
    u32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(hwnd) and not u32.IsIconic(hwnd):
            rc = wt.RECT()
            u32.GetClientRect(hwnd, ctypes.byref(rc))  # zone de jeu, sans la barre de titre
            pt = wt.POINT(0, 0)
            u32.ClientToScreen(hwnd, ctypes.byref(pt))
            if rc.right > 200 and rc.bottom > 200:
                found.append((pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom))
        return True

    u32.EnumWindows(each, 0)
    # la plus grande fenetre du jeu (pas une petite fenetre d'erreur ou de lanceur)
    return max(found, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), default=None)


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
    err = (r.stderr or "").strip()
    m = re.search(r"OCR_LANG (\S+)", err)
    ocr.last_info = (f"langue OCR {m.group(1)}" if m else
                     "OCR de Windows indisponible" if "OCR_INDISPONIBLE" in err else
                     f"erreur OCR : {err[:200] or 'code ' + str(r.returncode)}")
    return json.loads(out) if out.startswith("[") else []


ocr.last_info = ""


def columns(lines, width):
    """Regroupe les lignes lues par carte de recompense, selon leur position reelle a l'ecran :
    les lignes d'un meme nom sont centrees au meme endroit, les cartes sont espacees.
    Marche avec 1 a 4 joueurs (les cartes sont centrees, pas reparties sur tout l'ecran)."""
    gap = width * 0.06
    cols = []
    for ln in sorted(lines, key=lambda l: l["x"] + l["w"] / 2):
        cx = ln["x"] + ln["w"] / 2
        if cols and cx - cols[-1]["cx"] < gap:
            cols[-1]["lines"].append(ln)
            n = len(cols[-1]["lines"])
            cols[-1]["cx"] += (cx - cols[-1]["cx"]) / n  # centre moyen
        else:
            cols.append({"cx": cx, "lines": [ln]})
    return [c["lines"] for c in cols]


def text_groups(col):
    """Morceaux de texte a essayer pour une carte : chaque ligne seule, et deux lignes qui se
    suivent (nom sur deux lignes, ex. "Paris Prime - Partie" / "Inferieure")."""
    col = sorted(col, key=lambda ln: ln["y"])
    groups = [[ln] for ln in col]
    for a, b in zip(col, col[1:]):
        if b["y"] - (a["y"] + a["h"]) < max(a["h"], b["h"]) * 1.2:
            groups.append([a, b])
    return groups


class RewardOverlay:
    def __init__(self, app):
        self.app = app            # module app : game(), catalog(), prices(), relic tables...
        self.enabled = True
        self.relics = {}          # chemin de relique -> derniere fois vue
        self.known = []           # recompenses donnees par le journal pour l'ecran en cours
        self.window = None
        self.status = "pret : en attente d'un ecran de recompenses"
        self.last = None          # dernier resultat (pour l'interface)
        self.log_dir = None       # dossier de l'app : overlay.log + derniere capture lue

    def log(self, msg):
        """Journal de diagnostic (overlay.log) : pourquoi l'overlay s'est affiche ou non."""
        print("overlay :", msg)
        if not self.log_dir:
            return
        try:
            f = Path(self.log_dir) / "overlay.log"
            if f.exists() and f.stat().st_size > 300_000:  # on garde la fin
                f.write_text(f.read_text(encoding="utf-8", errors="replace")[-150_000:], encoding="utf-8")
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except Exception:
            pass

    # ---------------------------------------------------------------- journal du jeu
    def follow_log(self):
        self.log(f"demarrage, journal du jeu : {LOG} ({'trouve' if LOG.exists() else 'INTROUVABLE'})")
        if not LOG.exists():
            self.status = "journal du jeu introuvable : lance Warframe une fois"
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
                if not self.status.startswith("journal illisible"):
                    self.log(f"journal du jeu illisible : {e}")
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
        if OPEN_MARK in line:
            if not self.enabled:
                self.log("ecran de recompenses detecte, mais l'overlay est desactive")
                return
            squad = [p.rsplit("/", 1)[-1] for p, t in self.relics.items() if time.time() - t < RELIC_MEMORY]
            self.log(f"ecran de recompenses detecte ; reliques de l'escouade vues : {squad or 'aucune'}")
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
            # la fenetre du jeu, sur l'ecran ou elle se trouve (sinon l'ecran principal)
            rect = find_game_rect()
            shot = ImageGrab.grab(bbox=rect, all_screens=True) if rect else ImageGrab.grab()
            self.log(f"fenetre du jeu : {rect or 'introuvable, ecran principal utilise'}")
            if not rect:
                rect = (0, 0, shot.width, shot.height)
            self.rect = rect
            W, H = shot.size
            top, bottom = int(H * 0.22), int(H * 0.62)  # bande des noms des cartes
            band = shot.crop((0, top, W, bottom))
            band = band.resize((band.width * 2, band.height * 2))  # texte plus gros = OCR plus fiable
            path = Path(tempfile.gettempdir()) / "inventaire_tenno_recompenses.png"
            band.save(path)
            if self.log_dir:  # derniere capture lue, pour comprendre un echec
                band.save(Path(self.log_dir) / "overlay_derniere_capture.png")
            self.status = "lecture de l'ecran"
            lines = ocr(path, "fr-FR" if lang == "fr" else "en-US")
            self.log(f"{ocr.last_info} ; {len(lines)} ligne(s) lue(s) : {[ln['t'] for ln in lines][:20]}")
            for ln in lines:  # retour en coordonnees ecran
                ln["x"], ln["w"] = ln["x"] / 2, ln["w"] / 2
                ln["y"], ln["h"] = ln["y"] / 2 + top, ln["h"] / 2
            self.show_rewards(lines, W, H, lang)
        except Exception as e:
            import traceback
            self.status = f"erreur : {e}"
            self.log("ERREUR " + traceback.format_exc().replace("\n", " | "))

    def show_rewards(self, lines, W, H, lang):
        cands, squad = self.candidates(lang)
        rewards = []
        for col in columns(lines, W):
            # le meilleur morceau de texte de la carte (le nom), sans le pseudo du joueur en dessous
            path, score, used = None, 0, None
            for grp in text_groups(col):
                p2, s2 = self.match(" ".join(ln["t"] for ln in grp), cands)
                if s2 > score:
                    path, score, used = p2, s2, grp
            texte = " ".join(ln["t"] for ln in used) if used else " ".join(ln["t"] for ln in col)
            self.log(f"  carte '{texte}' -> {path.rsplit('/', 1)[-1] if path else 'rien'} (score {score:.2f})")
            if path and score >= 0.6:
                x = min(ln["x"] for ln in used) + max(ln["x"] + ln["w"] for ln in used)
                rewards.append({"chemin": path, "x": x / 2, "y": min(ln["y"] for ln in used),
                                "score": round(score, 2), **self.value(path, lang)})
        best = max((r["prix"] or 0 for r in rewards), default=0)
        for r in rewards:
            r["meilleur"] = best > 0 and r["prix"] == best
        self.last = {"t": time.time(), "recompenses": rewards, "escouade": len(squad)}
        heure = time.strftime("%H:%M")
        self.status = (f"dernier ecran ({heure}) : {len(rewards)} recompense(s) reconnue(s)" if rewards else
                       f"dernier ecran ({heure}) : aucune recompense reconnue, voir overlay.log")
        self.log(f"resultat : {len(rewards)} reconnue(s) sur {len(columns(lines, W))} carte(s) lue(s), "
                 f"{len(cands)} recompenses possibles")
        if rewards and self.window:
            # positions relatives a la fenetre du jeu : l'overlay se pose exactement dessus
            self.window.show(rewards, SHOW_SECONDS, getattr(self, "rect", None))

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
        return {"nom": g.name(path, lang) or en, "prix": price, "ducats": ducats,
                "image": self.image_bytes(slug, path)}

    _images = {}

    def image_bytes(self, slug, path):
        """Image de l'item (Warframe Market, sinon icone du jeu), gardee en memoire."""
        key = slug or path
        if key in self._images:
            return self._images[key]
        app = self.app
        thumb, big = app.catalog().images(slug) if slug else (None, None)
        url = ("https://warframe.market/static/assets/" + (big or thumb)) if (big or thumb) \
            else app.game().icon_url(path)
        data = None
        if url:
            try:
                r = app.requests.get(url, timeout=4)
                data = r.content if r.ok else None
            except Exception:
                pass
        self._images[key] = data
        return data

    def start(self, assets=None, log_dir=None):
        self.log_dir = log_dir
        set_dpi_aware()
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

    def show(self, rewards, seconds, rect=None):
        """rect = (gauche, haut, droite, bas) de la fenetre du jeu, en pixels ecran."""
        self.q.put(("show", rewards, seconds, rect))

    def run(self):
        import tkinter as tk
        set_dpi_aware()  # memes pixels que la capture
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

        def click_through():
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            GWL_EXSTYLE = -20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # calque + transparent aux clics + pas dans la barre des taches + ne prend pas le focus
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                                style | 0x80000 | 0x20 | 0x80 | 0x8000000)

        GOLD, PLAT, TEXT, MUTED, PANEL, BORDER = "#c9a96e", "#7fd4e6", "#ece6d6", "#8d8878", "#0e131e", "#3a3a40"
        keep = []  # references des images (sinon tkinter les efface)

        def icon(name, size):
            from PIL import Image, ImageTk
            im = Image.open(self.assets / f"{name}.png").convert("RGBA").resize((size, size), Image.LANCZOS)
            ph = ImageTk.PhotoImage(im, master=root)
            keep.append(ph)
            return ph

        def item_image(data, size):
            import io
            from PIL import Image, ImageTk
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            im.thumbnail((size, size), Image.LANCZOS)
            ph = ImageTk.PhotoImage(im, master=root)
            keep.append(ph)
            return ph

        def card_shape(x1, y1, x2, y2, cut=14):
            """Rectangle aux angles biseautes (comme les menus du jeu)."""
            return [x1 + cut, y1, x2, y1, x2, y2 - cut, x2 - cut, y2, x1, y2, x1, y1 + cut]

        def draw(rewards, GW, GH):
            """Grandes cases centrees en bas de la fenetre du jeu, taille adaptee au nombre (1 a 4)."""
            canvas.delete("all")
            keep.clear()
            n = max(1, len(rewards))
            gap = int(GW * 0.012)
            cw = int(min(GW * 0.19, (GW * 0.82 - gap * (n - 1)) / n))   # largeur d'une case
            ch = int(GH * 0.30)                                           # hauteur d'une case
            total = n * cw + (n - 1) * gap
            x0, y0 = (GW - total) / 2, GH - ch - GH * 0.05
            s = ch / 320  # echelle des textes (320 px de haut = taille de reference)
            for i, r in enumerate(sorted(rewards, key=lambda r: r["x"])):  # meme ordre qu'a l'ecran
                x1, y1 = x0 + i * (cw + gap), y0
                x2, y2 = x1 + cw, y1 + ch
                cx = (x1 + x2) / 2
                gold = r.get("meilleur")
                canvas.create_polygon(card_shape(x1, y1, x2, y2), fill=PANEL,
                                      outline=GOLD if gold else BORDER, width=3 if gold else 2)
                top = y1 + 10 * s
                if gold:  # bandeau du meilleur choix
                    canvas.create_polygon(card_shape(x1 + 3, y1 + 3, x2 - 3, y1 + 30 * s, 11), fill=GOLD, outline="")
                    canvas.create_text(cx, y1 + 17 * s, text="MEILLEUR CHOIX", fill=PANEL,
                                       font=("Segoe UI Semibold", int(12 * s)))
                    top = y1 + 38 * s
                img_size = int(ch * 0.42)
                if r.get("image"):
                    try:
                        canvas.create_image(cx, top + img_size / 2, image=item_image(r["image"], img_size))
                    except Exception:
                        pass
                name_y = top + img_size + 8 * s
                canvas.create_text(cx, name_y, text=r["nom"], fill=TEXT, anchor="n", justify="center",
                                   width=cw - 24, font=("Segoe UI Semibold", int(22 * s)))
                # ligne du bas : platine en grand, ducats a droite
                by = y2 - 30 * s
                parts = []
                if r["prix"] is not None:
                    parts.append(("platine", str(r["prix"]), PLAT, int(30 * s)))
                if r["ducats"]:
                    parts.append(("ducat", str(r["ducats"]), GOLD, int(21 * s)))
                if not parts:
                    parts.append((None, "non vendable", MUTED, int(15 * s)))
                ids, x = [], 0
                for ic, txt, color, size in parts:
                    if ic and self.assets:
                        try:
                            ids.append(canvas.create_image(x, by, image=icon(ic, int(size * 1.15)), anchor="w"))
                            x += size * 1.15 + 6
                        except Exception:
                            pass
                    t = canvas.create_text(x, by, text=txt, fill=color, anchor="w",
                                           font=("Segoe UI Semibold", size))
                    ids.append(t)
                    x = canvas.bbox(t)[2] + 20 * s
                bx1, _, bx2, _ = canvas.bbox(*ids)
                for it in ids:
                    canvas.move(it, cx - (bx1 + bx2) / 2, 0)

        def tick():
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "show":
                        L, T, R, B = cmd[3] or (0, 0, W, H)
                        # se place sur l'ecran du jeu (ecran de gauche = coordonnees negatives)
                        root.geometry(f"{R - L}x{B - T}{L:+d}{T:+d}")
                        canvas.config(width=R - L, height=B - T)
                        draw(cmd[1], R - L, B - T)
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
