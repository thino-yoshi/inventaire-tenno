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
GETS_RE = re.compile(r"VoidProjections: (\S+) gets reward (/Lotus/\S+)")   # TA recompense (exacte)
INFO_RE = re.compile(r"VoidProjections: Client got reward info from (\S+)")   # un joueur de l'escouade
ALL_MARK = "VoidProjections: Client has reward info for all players now"
OPEN_MARK = "ProjectionRewardChoice.lua: Relic rewards initialized"
CLOSE_MARK = "ProjectionRewardChoice.lua: Relic reward screen shut down"
SURE, DOUTE = 0.8, 0.62    # score de lecture : sur / douteux (carte « ? ») / pas une carte
SHOW_SECONDS = 16          # le choix dure 15 s
FICHE_SECONDS = 8          # prix affiches sur une fiche de relique (touche Alt + A)
HOTKEY = (0x0001 | 0x4000, 0x41)  # MOD_ALT | MOD_NOREPEAT, touche A
# titre de la liste sur la fiche d'une relique, dans les langues de l'OCR
FICHE_TITRES = ("recompenses possibles", "possible rewards")
RELIC_MEMORY = 30 * 60     # reliques vues dans les 30 dernieres minutes = escouade actuelle

# OCR integre a Windows (Windows.Media.Ocr) appele depuis PowerShell : sortie JSON des lignes lues
OCR_PS = r"""
param($lang)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(); $t.Result }
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($lang))
# langue demandee absente (ex. Windows anglais sans pack francais) : langue(s) du compte Windows
if (-not $eng) { $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $eng) { [Console]::Out.WriteLine('OCR_INDISPONIBLE'); exit 2 }
[Console]::Out.WriteLine('OCR_LANG ' + $eng.RecognizerLanguage.LanguageTag)
while ($true) {
$path = [Console]::In.ReadLine()
if ($path -eq $null) { break }
try {
# lue en memoire : le fichier n'est jamais garde ouvert (la capture suivante peut l'ecraser)
$bytes = [IO.File]::ReadAllBytes($path)
$stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream([IO.MemoryStream]::new($bytes))
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
$stream.Dispose()
$out = @(foreach ($l in $res.Lines) {
  $r = $l.Words | ForEach-Object { $_.BoundingRect }
  $x = ($r | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
  $y = ($r | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
  $x2 = ($r | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
  $y2 = ($r | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
  [pscustomobject]@{ t = $l.Text; x = $x; y = $y; w = $x2 - $x; h = $y2 - $y } })
[Console]::Out.WriteLine((ConvertTo-Json -InputObject $out -Compress))
} catch { [Console]::Out.WriteLine('ERREUR ' + ($_.Exception.Message -replace '\s+', ' ')) }
[Console]::Out.Flush()
}
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
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^\d+ x |  ?x \d+$", "", s).strip()  # "2 X Forma", "Forma X 2"


class OcrWorker:
    """PowerShell lance une seule fois et garde le moteur OCR de Windows charge : une image lue
    prend ~0,2 s au lieu de ~1 s (demarrage de PowerShell + chargement de WinRT a chaque fois)."""

    def __init__(self):
        self.proc, self.lang, self.info = None, None, ""
        self.lines = queue.Queue()
        self.lock = threading.Lock()

    def _start(self, lang):
        self.stop()
        script = Path(tempfile.gettempdir()) / "inventaire_tenno_ocr.ps1"
        script.write_text(OCR_PS, encoding="utf-8-sig")
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), lang],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.lang, self.lines = lang, queue.Queue()
        out, lines = self.proc.stdout, self.lines

        def lire():  # lecture dans un fil a part : permet un delai maximum par image
            for ln in out:
                lines.put(ln.strip())
            lines.put(None)
        threading.Thread(target=lire, daemon=True).start()
        first = self._next(20)
        if first and first.startswith("OCR_LANG "):
            self.info = "langue OCR " + first[9:]
        else:
            self.info = "OCR de Windows indisponible" if first == "OCR_INDISPONIBLE" else f"erreur OCR : {first}"
            self.stop()

    def _next(self, timeout):
        while True:
            try:
                ln = self.lines.get(timeout=timeout)
            except queue.Empty:
                return None
            if ln != "":
                return ln

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def warm(self, lang):
        with self.lock:
            if not self.proc or self.proc.poll() is not None or self.lang != lang:
                self._start(lang)

    def read(self, image_path, lang):
        """Lignes lues dans l'image : [{t, x, y, w, h}] (coordonnees de l'image)."""
        with self.lock:
            for _ in range(2):  # PowerShell ferme entre-temps : on le relance une fois
                if not self.proc or self.proc.poll() is not None or self.lang != lang:
                    self._start(lang)
                if not self.proc:
                    return []
                try:
                    self.proc.stdin.write(os.path.abspath(image_path) + "\n")  # l'OCR refuse les "/"
                    self.proc.stdin.flush()
                except OSError:
                    self.stop()
                    continue
                out = self._next(10)
                if out is None:
                    self.stop()
                    continue
                if out.startswith("ERREUR"):
                    self.info = "erreur OCR : " + out[:200]
                    return []
                return json.loads(out) if out.startswith("[") else []
            return []


OCR = OcrWorker()


def ocr(image_path, lang="fr-FR"):
    lines = OCR.read(image_path, lang)
    ocr.last_info = OCR.info
    return lines


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
        self.ecran = 0            # numero de l'ecran de recompenses en cours (annule les anciens)
        self.index = {}           # langue -> [(chemin, noms normalises)] de TOUTES les recompenses
        self.rect_cache = (0, None)
        self.players = set()      # joueurs de l'ecran en cours (solo = 1)
        self.journal_only = 0     # ecran affiche depuis le journal seul (solo) : pas d'OCR
        self.on_reward = None     # appele a la fermeture de l'ecran : ajout a l'inventaire de l'app

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
            time.sleep(0.1)

    def on_line(self, line):
        m = RELIC_RE.search(line)
        if m and m.group(1) in self.app.game().ex["relics"]:
            if m.group(1) not in self.relics:
                threading.Thread(target=self.prefetch, args=([m.group(1)],), daemon=True).start()
            self.relics[m.group(1)] = time.time()
            return
        m = GETS_RE.search(line)
        if m:
            self.known.append(m.group(2).replace("/StoreItems/", "/", 1))
            self.players.add(m.group(1))
            return
        m = INFO_RE.search(line)
        if m:
            self.players.add(m.group(1))
            return
        if ALL_MARK in line:
            # seul dans la mission : une seule carte, la tienne, connue par le journal -> instantane
            if self.enabled and len(self.players) <= 1 and self.known:
                self.journal_only = self.ecran
                threading.Thread(target=self.show_solo, args=(self.ecran,), daemon=True).start()
            return
        if CLOSE_MARK in line:
            self.finish(self.ecran)
            return
        if OPEN_MARK in line:
            if not self.enabled:
                self.log("ecran de recompenses detecte, mais l'overlay est desactive")
                return
            squad = [p.rsplit("/", 1)[-1] for p, t in self.relics.items() if time.time() - t < RELIC_MEMORY]
            self.log(f"ecran de recompenses detecte ; reliques de l'escouade vues : {squad or 'aucune'}")
            self.known = []
            self.players = set()
            self.last = None
            self.ecran += 1
            threading.Thread(target=self.scan, args=(self.ecran,), daemon=True).start()

    # ---------------------------------------------------------------- recompenses possibles
    def reward_paths(self, relics):
        g = self.app.game()
        paths = set()
        for rp in relics:
            manifest = g.ex["relics"][rp].get("rewardManifest")
            for r in (g.rewards().get(manifest) or [[]])[0]:
                paths.add(r["type"].replace("/StoreItems/", "/", 1))
        return paths

    def all_rewards(self, lang):
        """[(chemin, noms normalises)] de toutes les recompenses de reliques (calcule une fois)."""
        if lang not in self.index:
            g = self.app.game()
            out = []
            for p in self.reward_paths(list(g.ex["relics"])):
                names = {norm(g.name(p, l)) for l in {lang, "en"} if g.name(p, l)}
                if names:
                    out.append((p, names))
            self.index[lang] = out
        return self.index[lang]

    def candidates(self, lang):
        """(toutes les recompenses, chemins de l'escouade, reliques de l'escouade)."""
        now = time.time()
        squad = [p for p, t in self.relics.items() if now - t < RELIC_MEMORY]
        likely = self.reward_paths(squad) | set(self.known)
        return self.all_rewards(lang), likely, squad

    def match(self, text, cands, likely=()):
        t = norm(text)
        if len(t) < 4:
            return None, 0
        best, score = None, 0
        sm = difflib.SequenceMatcher(None, "", t)  # le texte lu est compare a chaque nom
        for p, names in cands:
            bonus = 0.04 if p in likely else 0  # a egalite, une recompense des reliques vues gagne
            for n in names:
                sm.set_seq1(n)
                if sm.real_quick_ratio() + bonus <= score or sm.quick_ratio() + bonus <= score:
                    continue
                s = sm.ratio() + bonus
                if s > score:
                    best, score = p, s
        return best, min(score, 1.0)

    # ---------------------------------------------------------------- capture + OCR
    def game_rect(self):
        """Position de la fenetre du jeu, gardee 30 s (la chercher prend ~50 ms)."""
        t, rect = self.rect_cache
        if not rect or time.time() - t > 30:
            rect = find_game_rect()
            self.rect_cache = (time.time(), rect)
        return rect

    def grab(self, n):
        """Capture + OCR de la bande des noms. (lignes en coordonnees ecran, largeur, hauteur)."""
        from PIL import ImageGrab
        lang = self.app.CURRENT_LANG[0]
        rect = self.game_rect()
        shot = ImageGrab.grab(bbox=rect, all_screens=True) if rect else ImageGrab.grab()
        if not rect:
            rect = (0, 0, shot.width, shot.height)
        self.rect = rect
        W, H = shot.size
        top, bottom = int(H * 0.22), int(H * 0.62)  # bande des noms des cartes
        band = shot.crop((0, top, W, bottom)).convert("RGB")
        k = 2 if H <= 1440 else 1  # texte agrandi en 1080p/1440p, deja assez gros en 4K (image 4x moins lourde)
        if k > 1:
            band = band.resize((band.width * k, band.height * k))  # texte plus gros = OCR plus fiable
        path = Path(tempfile.gettempdir()) / f"inventaire_tenno_recompenses{n % 2}.bmp"
        band.save(path)  # BMP : ~0,01 s au lieu de ~0,5 s en PNG
        lines = ocr(path, "fr-FR" if lang == "fr" else "en-US")
        for ln in lines:  # retour en coordonnees ecran
            ln["x"], ln["w"] = ln["x"] / k, ln["w"] / k
            ln["y"], ln["h"] = ln["y"] / k + top, ln["h"] / k
        return lines, W, H, lang, band

    def scan(self, ecran):
        """Lit l'ecran des que les cartes apparaissent : un essai toutes les ~0,3 s pendant 4 s,
        puis encore un ou deux essais pour attraper une carte affichee un peu plus tard."""
        t0 = time.time()
        time.sleep(0.3)
        self.status = "lecture de l'ecran"
        shown, extra, n, band = 0, 0, 0, None
        while self.ecran == ecran and time.time() - t0 < 5:
            if self.journal_only == ecran:
                self.log("  solo : affiche depuis le journal, sans lire l'ecran")
                return
            n += 1
            try:
                t1 = time.time()
                lines, W, H, lang, band = self.grab(n)
                t2 = time.time()
                rewards = self.show_rewards(lines, W, H, lang, quiet=n > 1 and not shown)
                t3 = time.time()
            except Exception:
                import traceback
                self.status = "erreur de lecture"
                self.log("ERREUR " + traceback.format_exc().replace("\n", " | "))
                return
            if len(rewards) > shown:
                self.log(f"  affiche apres {time.time() - t0:.2f} s (essai {n} ; capture + lecture {t2 - t1:.2f} s, "
                         f"rapprochement {t3 - t2:.2f} s) : {len(rewards)} recompense(s)")
                shown = len(rewards)
                if self.window:
                    self.window.show(rewards, SHOW_SECONDS - (time.time() - t0), getattr(self, "rect", None))
                threading.Thread(target=self.complete, args=(rewards, ecran, t0, lang), daemon=True).start()
            if shown:
                extra += 1
                if shown >= 4 or extra > 2:
                    break
                time.sleep(0.4)
            else:
                time.sleep(0.2)
        if not shown:
            self.log(f"  rien reconnu en {time.time() - t0:.1f} s ({n} essai(s))")
        if band is not None and self.log_dir:  # derniere capture lue, pour comprendre un echec
            try:
                band.save(Path(self.log_dir) / "overlay_derniere_capture.png")
            except Exception:
                pass

    def show_solo(self, ecran):
        """Seul dans la mission : la carte est la tienne, connue exactement par le journal."""
        lang = self.app.CURRENT_LANG[0]
        own = self.known[0]
        r = {"chemin": own, "x": 0, "y": 0, "score": 1.0, "a_moi": True, **self.value(own, lang, rapide=True)}
        r["meilleur"] = bool(r["prix"])
        self.last = {"t": time.time(), "recompenses": [r], "escouade": 1}
        self.status = f"dernier ecran ({time.strftime('%H:%M')}) : solo, recompense lue dans le journal"
        self.log(f"  solo : {own.rsplit('/', 1)[-1]} (journal, sans OCR)")
        try:
            self.rect = self.game_rect()
        except Exception:
            pass
        if self.window and self.enabled:
            self.window.show([r], SHOW_SECONDS, getattr(self, "rect", None))
        threading.Thread(target=self.complete, args=([r], ecran, time.time(), lang), daemon=True).start()

    def finish(self, ecran):
        """Ecran de recompenses ferme : on passe le resultat a l'app (ajout provisoire a l'inventaire)."""
        own = self.known[0] if self.known else None
        if not own or not self.on_reward:
            return
        cartes = sorted((self.last or {}).get("recompenses") or [], key=lambda r: r["x"])
        if not any(r.get("chemin") == own for r in cartes):  # ecran non lu : au moins ta carte
            cartes = [{"chemin": own, "a_moi": True, "x": 0, **self.value(own, self.app.CURRENT_LANG[0])}] + cartes
        try:
            self.on_reward({"propre": own, "cartes": [{"chemin": r.get("chemin"), "nom": r.get("nom"),
                                                       "prix": r.get("prix"), "a_moi": r.get("chemin") == own}
                                                      for r in cartes]})
        except Exception as e:
            self.log(f"ajout a l'inventaire impossible : {e}")

    def complete(self, rewards, ecran, t0, lang):
        """Apres le 1er affichage (prix deja connus seulement) : prix manquants, « Tu en as »,
        pieces du set et images, calcules en arriere-plan, puis l'overlay est redessine."""
        t1 = time.time()
        changed = False
        for r in rewards:
            if r.get("chemin") and r.pop("_a_completer", False):
                try:
                    v = self.value(r["chemin"], lang)
                except Exception as e:
                    self.log(f"  details impossibles pour {r['chemin'].rsplit('/', 1)[-1]} : {e}")
                    continue
                for k in ("prix", "ducats", "possede", "pieces"):
                    if v.get(k) is not None and v[k] != r.get(k):
                        r[k], changed = v[k], True
            if r.get("image") is None and r.get("_key"):
                if r["_key"] not in self._images:
                    self.image_bytes(r.get("_slug"), r["chemin"])
                if self._images.get(r["_key"]) is not None:
                    r["image"], changed = self._images[r["_key"]], True
        if not changed:
            return
        best = max((r.get("prix") or 0 for r in rewards), default=0)
        for r in rewards:
            r["meilleur"] = best > 0 and r.get("prix") == best
        self.log(f"  details completes en {time.time() - t1:.2f} s")
        if self.ecran == ecran and self.window and time.time() - t0 < SHOW_SECONDS:
            self.window.show(rewards, SHOW_SECONDS - (time.time() - t0), getattr(self, "rect", None))

    def hotkey_loop(self):
        """Raccourci global Alt + A (marche aussi quand le jeu a le focus)."""
        import ctypes.wintypes as wt
        u32 = ctypes.windll.user32
        if not u32.RegisterHotKey(None, 1, *HOTKEY):
            self.log("raccourci Alt + A indisponible (deja pris par une autre application)")
            return
        self.log("raccourci Alt + A pret (prix sur une fiche de relique)")
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and self.enabled:  # WM_HOTKEY
                threading.Thread(target=self.scan_fiche, daemon=True).start()

    def scan_fiche(self):
        """Lit la fiche de relique ouverte a l'ecran et affiche le prix a droite de chaque nom."""
        from PIL import ImageGrab
        t0 = time.time()
        try:
            lang = self.app.CURRENT_LANG[0]
            rect = self.game_rect()
            shot = ImageGrab.grab(bbox=rect, all_screens=True) if rect else ImageGrab.grab()
            if not rect:
                rect = (0, 0, shot.width, shot.height)
            self.rect = rect
            # zone autour de la souris (la fiche s'ouvre a cote du curseur) : moins a lire = plus rapide
            import ctypes.wintypes as wt
            pt = wt.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            W, H = shot.size
            mx, my = pt.x - rect[0], pt.y - rect[1]
            if not (0 <= mx < W and 0 <= my < H):
                mx, my = W // 2, H // 2
            L, R = max(0, mx - int(W * 0.35)), min(W, mx + int(W * 0.35))
            T, B = max(0, my - int(H * 0.6)), min(H, my + int(H * 0.6))
            zone = shot.crop((L, T, R, B)).convert("RGB")
            k = 2 if H <= 1440 else 1  # texte agrandi en 1080p, deja assez gros en 4K
            if k > 1:
                zone = zone.resize((zone.width * k, zone.height * k))
            path = Path(tempfile.gettempdir()) / "inventaire_tenno_fiche.bmp"
            zone.save(path)
            lines = ocr(path, "fr-FR" if lang == "fr" else "en-US")
            for ln in lines:  # coordonnees dans la fenetre du jeu
                ln["x"], ln["w"] = ln["x"] / k + L, ln["w"] / k
                ln["y"], ln["h"] = ln["y"] / k + T, ln["h"] / k
            labels = self.fiche_labels(lines, lang, W)
            self.log(f"fiche Alt + A : {len(labels['rows']) if labels else 0} prix en {time.time() - t0:.2f} s ; "
                     f"{len(lines)} ligne(s) lue(s) : {[ln['t'] for ln in lines][:25]}")
            if self.window:
                self.window.labels(labels, FICHE_SECONDS, rect)
            if not labels and self.log_dir:
                zone.save(Path(self.log_dir) / "overlay_derniere_fiche.png")
        except Exception:
            import traceback
            self.log("ERREUR fiche " + traceback.format_exc().replace("\n", " | "))

    def fiche_labels(self, lines, lang, W):
        """Colonne de prix collee au bord droit de la fiche : {x, cote, size, titre_y, rows}.
        rows = [{y, prix, ducats, meilleur}], une ligne par nom de recompense lu."""
        cands, likely, _ = self.candidates(lang)
        titre = next((ln for ln in lines if any(t in norm(ln["t"]) for t in FICHE_TITRES)), None)
        if titre:  # la fiche : lignes alignees sur le titre (pas le reste de l'ecran)
            h0 = titre["h"]
            fiche = [ln for ln in lines if titre["x"] - 2 * h0 < ln["x"] < titre["x"] + 3 * h0]
            noms = [ln for ln in fiche if ln["y"] > titre["y"] + h0 / 2]
        else:
            fiche = noms = lines
        rows, used = [], []
        for ln in noms:
            path, score = self.match(ln["t"], cands)
            if not path or score < 0.8:
                continue
            v = self.value(path, lang)
            used.append(ln)
            rows.append({"y": ln["y"] + ln["h"] / 2, "prix": v["prix"], "ducats": v["ducats"]})
        if not rows:
            return None
        best = max((r["prix"] or 0 for r in rows), default=0)
        for r in rows:
            r["meilleur"] = best > 0 and r["prix"] == best
        h = sorted(ln["h"] for ln in used)[len(used) // 2]  # meme taille pour toutes les lignes
        # bord droit de la fiche ~ fin de la plus longue ligne de texte + sa marge
        droite = max(ln["x"] + ln["w"] for ln in (fiche or used)) + h * 1.6
        gauche = min(ln["x"] for ln in (fiche or used)) - h * 1.6
        return {"droite": droite, "gauche": gauche, "W": W, "size": h,
                "titre_y": titre["y"] + titre["h"] / 2 if titre else None, "rows": rows}

    def prefetch(self, relics):
        """Prix et images des recompenses d'une relique, prepares a l'avance."""
        try:
            lang = self.app.CURRENT_LANG[0]
            self.all_rewards(lang)
            for p in self.reward_paths(relics):
                v = self.value(p, lang)
                if v["image"] is None:
                    self.image_bytes(v["_slug"], p)
        except Exception as e:
            self.log(f"preparation des recompenses impossible : {e}")

    def show_rewards(self, lines, W, H, lang, quiet=False):
        cands, likely, squad = self.candidates(lang)
        rewards = []
        own = self.known[0] if self.known else None
        own_names = {norm(self.app.game().name(own, l)) for l in {lang, "en"} if own and self.app.game().name(own, l)}
        for col in columns(lines, W):
            # le meilleur morceau de texte de la carte (le nom), sans le pseudo du joueur en dessous
            path, score, used = None, 0, None
            for grp in text_groups(col):
                p2, s2 = self.match(" ".join(ln["t"] for ln in grp), cands, likely)
                if s2 > score:
                    path, score, used = p2, s2, grp
            texte = " ".join(ln["t"] for ln in used) if used else " ".join(ln["t"] for ln in col)
            if not quiet:
                self.log(f"  carte '{texte}' -> {path.rsplit('/', 1)[-1] if path else 'rien'} (score {score:.2f})")
            if path and score >= DOUTE:
                x = min(ln["x"] for ln in used) + max(ln["x"] + ln["w"] for ln in used)
                # ressemblance avec TA recompense (connue par le journal)
                # (ligne par ligne : le pseudo du joueur sous le nom ne doit pas compter)
                own_score = max((difflib.SequenceMatcher(None, norm(" ".join(l["t"] for l in grp)), n).ratio()
                                 for grp in text_groups(col) for n in own_names), default=0)
                r = {"x": x / 2, "y": min(ln["y"] for ln in used), "score": round(score, 2), "own_score": own_score}
                if score >= SURE:
                    r.update({"chemin": path, **self.value(path, lang, rapide=True)})
                else:  # lecture douteuse : on prefere « ? » a un mauvais item
                    r.update({"chemin": None, "nom": "?", "prix": None, "ducats": None, "image": None,
                              "_slug": None, "_key": None, "douteux": True})
                rewards.append(r)
        if own:  # une des cartes est forcement la tienne : on la corrige avec le vrai nom
            if not any(r["chemin"] == own for r in rewards) and rewards:
                # de preference une carte douteuse qui lui ressemble ; une carte sure seulement si
                # elle lui ressemble vraiment (sinon on remplacerait la carte juste d'un autre joueur)
                douteuses = [r for r in rewards if r.get("douteux")]
                cible = max(douteuses or rewards, key=lambda r: r["own_score"])
                if (cible.get("douteux") and cible["own_score"] >= 0.45) or cible["own_score"] >= 0.75:
                    cible.update({"chemin": own, "douteux": False, **self.value(own, lang, rapide=True)})
            for r in rewards:
                r["a_moi"] = r["chemin"] == own
        best = max((r["prix"] or 0 for r in rewards), default=0)
        for r in rewards:
            r["meilleur"] = best > 0 and r["prix"] == best
        if rewards or not self.last:
            self.last = {"t": time.time(), "recompenses": rewards, "escouade": len(squad)}
        heure = time.strftime("%H:%M")
        self.status = (f"dernier ecran ({heure}) : {len(rewards)} recompense(s) reconnue(s)" if rewards else
                       f"dernier ecran ({heure}) : aucune recompense reconnue, voir overlay.log")
        if not quiet:
            self.log(f"resultat : {len(rewards)} reconnue(s) sur {len(columns(lines, W))} carte(s) lue(s), "
                     f"{len(likely)} recompenses des reliques vues")
        return rewards

    def value(self, path, lang, rapide=False):
        """Nom, prix, ducats, image... d'une recompense.
        rapide=True : instantane pour le 1er affichage (prix deja connus seulement, sans internet
        ni calcul de l'inventaire) ; le reste est complete ensuite par complete()."""
        app = self.app
        g, cat = app.game(), app.catalog()
        en = g.name(path, "en")
        slug = cat.find(path, en)
        price = None
        if slug and rapide:
            hit = app._prices.get(f"{slug}|None|None")
            try:
                shared = app.shared_prices(slug) if app._shared["items"] else None
            except Exception:
                shared = None
            best = max((x for x in (hit, shared) if x), key=lambda x: x.get("t", 0), default=None)
            price = best["min"] if best else None
        elif slug:
            try:
                price = app.prices(slug, cached_ok=True)["min"]
            except Exception:
                pass
        ducats = cat.by_slug.get(slug, {}).get("ducats") if slug else None
        key = slug or path
        if rapide:
            info = {"possede": None, "pieces": None}
        else:
            try:  # combien on en a, et ou en est son set (pieces ✓ / ✗)
                info = app.overlay_set_info(path, lang)
            except Exception:
                info = {"possede": None, "pieces": None}
        out = {"nom": g.name(path, lang) or en, "prix": price, "ducats": ducats,
               "image": self._images.get(key), "_slug": slug, "_key": key,
               "possede": info.get("possede"), "pieces": info.get("pieces")}
        if rapide:
            out["_a_completer"] = True
        return out

    _images = {}

    def image_bytes(self, slug, path):
        """Image de l'item (Warframe Market, sinon icone du jeu), gardee en memoire."""
        key = slug or path
        if key in self._images:
            return self._images[key]
        app = self.app
        thumb, big = app.catalog().images(slug) if slug else (None, None)
        url = big or thumb
        if url and not url.startswith("http"):  # chemin relatif aux images de WFM (sinon icone du jeu)
            url = "https://warframe.market/static/assets/" + url
        url = url or app.game().icon_url(path)
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

        def warm():  # moteur OCR et liste des recompenses prets avant le premier ecran
            try:
                lang = self.app.CURRENT_LANG[0]
                OCR.warm("fr-FR" if lang == "fr" else "en-US")
                self.all_rewards(lang)
            except Exception as e:
                self.log(f"preparation impossible : {e}")
        threading.Thread(target=warm, daemon=True).start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
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

    def labels(self, labels, seconds, rect=None):
        """Petites etiquettes de prix a droite des noms d'une fiche de relique."""
        self.q.put(("labels", labels, seconds, rect))

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
        GREEN, RED = "#5cc98a", "#e06c6c"
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
            avec_set = any(r.get("pieces") for r in rewards)
            ch = int(GH * (0.37 if avec_set else 0.30))                   # hauteur d'une case
            total = n * cw + (n - 1) * gap
            x0, y0 = (GW - total) / 2, GH - ch - GH * 0.05
            s = int(GH * 0.30) / 320  # echelle des textes (320 px de haut = taille de reference)
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
                img_size = int(GH * 0.30 * 0.42)
                if r.get("image"):
                    try:
                        canvas.create_image(cx, top + img_size / 2, image=item_image(r["image"], img_size))
                    except Exception:
                        pass
                name_y = top + img_size + 8 * s
                nid = canvas.create_text(cx, name_y, text=r["nom"], fill=TEXT, anchor="n", justify="center",
                                         width=cw - 24, font=("Segoe UI Semibold", int(22 * s)))
                # combien on en a + pieces du set : ✓ possedee, ✗ manquante, ★ celle de la carte
                yy = canvas.bbox(nid)[3] + 6 * s
                if r.get("possede") is not None and not r.get("douteux"):
                    canvas.create_text(cx, yy, text=f"Tu en as : {r['possede']}", anchor="n",
                                       fill=GREEN if r["possede"] else MUTED, font=("Segoe UI Semibold", int(14 * s)))
                    yy += 22 * s
                if r.get("pieces"):
                    f = ("Segoe UI Semibold", int(13 * s))
                    ligne, lignes = [], []
                    for pc in r["pieces"]:
                        ok = pc["possede"] >= pc["requis"]
                        mot = ("★ " if pc["ici"] else "✓ " if ok else "✗ ") + pc["nom"]
                        couleur = GOLD if pc["ici"] else GREEN if ok else RED
                        ligne.append((mot, couleur))
                        if len(ligne) == 2:
                            lignes.append(ligne)
                            ligne = []
                    if ligne:
                        lignes.append(ligne)
                    for lg in lignes:  # deux pieces par ligne, centrees
                        ids, x = [], 0
                        for mot, couleur in lg:
                            t = canvas.create_text(x, yy, text=mot, fill=couleur, anchor="nw", font=f)
                            ids.append(t)
                            x = canvas.bbox(t)[2] + 12 * s
                        bx1, _, bx2, _ = canvas.bbox(*ids)
                        for it in ids:
                            canvas.move(it, cx - (bx1 + bx2) / 2, 0)
                        yy += 19 * s
                # ligne du bas : platine en grand, ducats a droite
                by = y2 - 30 * s
                parts = []
                if r["prix"] is not None:
                    parts.append(("platine", str(r["prix"]), PLAT, int(30 * s)))
                if r["ducats"]:
                    parts.append(("ducat", str(r["ducats"]), GOLD, int(21 * s)))
                if not parts:
                    parts.append((None, "nom illisible" if r.get("douteux") else "non vendable", MUTED, int(15 * s)))
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

        def draw_labels(data):
            """Une colonne de prix bien alignee, collee au bord droit de la fiche (ou au bord
            gauche s'il n'y a pas la place) : icones en en-tete, chiffres alignes a droite."""
            import tkinter.font as tkfont
            canvas.delete("all")
            keep.clear()
            if not data:
                return
            px = data["size"]                       # hauteur d'une ligne de texte du jeu, en pixels
            f = tkfont.Font(root=root, family="Segoe UI Semibold", size=-int(px * 0.78))
            fb = tkfont.Font(root=root, family="Segoe UI Semibold", size=-int(px * 0.78), weight="bold")
            ic = int(px * 0.9)
            pad, gap = int(px * 0.55), int(px * 0.9)
            num = f.measure("9999")
            col1 = max(num, ic)                     # colonne platine
            col2 = max(f.measure("100"), ic)        # colonne ducats
            width = pad + col1 + gap + col2 + pad
            x1 = data["droite"]
            if x1 + width > data["W"] - 4:         # pas la place a droite : a gauche de la fiche
                x1 = max(4, data["gauche"] - width)
            rows = data["rows"]
            top_y = data["titre_y"] if data["titre_y"] is not None else rows[0]["y"]
            y1, y2 = top_y - px * 0.85, rows[-1]["y"] + px * 0.85
            canvas.create_polygon(card_shape(x1, y1, x1 + width, y2, int(px * 0.5)),
                                  fill=PANEL, outline=BORDER, width=1)
            c1r, c2r = x1 + pad + col1, x1 + pad + col1 + gap + col2   # bords droits des colonnes
            # en-tete : les icones, a la hauteur de « Recompenses possibles »
            if self.assets:
                for name, right, w in (("platine", c1r, col1), ("ducat", c2r, col2)):
                    try:
                        canvas.create_image(right - w / 2, top_y if data["titre_y"] is not None
                                            else y1 + px * 0.6, image=icon(name, ic))
                    except Exception:
                        pass
            for r in rows:
                y = r["y"]
                if r["meilleur"]:  # meilleur choix : fond dore discret + chiffres dores
                    canvas.create_rectangle(x1 + 2, y - px * 0.55, x1 + width - 2, y + px * 0.55,
                                            fill="#2a2416", outline="")
                    canvas.create_rectangle(x1 + 2, y - px * 0.55, x1 + 5, y + px * 0.55, fill=GOLD, outline="")
                prix = "–" if r["prix"] is None else str(r["prix"])
                canvas.create_text(c1r, y, text=prix, anchor="e", font=fb if r["meilleur"] else f,
                                   fill=GOLD if r["meilleur"] else (PLAT if r["prix"] is not None else MUTED))
                canvas.create_text(c2r, y, text=str(r["ducats"]) if r["ducats"] else "–", anchor="e", font=f,
                                   fill=TEXT if r["ducats"] else MUTED)

        def tick():
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "labels":
                        L, T, R, B = cmd[3] or (0, 0, W, H)
                        root.geometry(f"{R - L}x{B - T}{L:+d}{T:+d}")
                        canvas.config(width=R - L, height=B - T)
                        draw_labels(cmd[1])
                        root.deiconify()
                        root.attributes("-topmost", True)
                        click_through()
                        state["hide_at"] = time.time() + (cmd[2] if cmd[1] else 0.1)
                    elif cmd[0] == "show":
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
