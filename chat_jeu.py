"""
Chat du jeu recopie dans l'app, avec traduction.

Le jeu n'ecrit pas le texte du chat dans son journal : on le lit a l'ecran, comme l'overlay des
reliques (capture + OCR de Windows). Pour que l'OCR lise bien malgre le decor du jeu derriere le
chat transparent, on ne garde que les couleurs du chat : blanc pour les messages, vert-bleu pour
les pseudos. Les pseudos et les messages sont lus separement (un passage par couleur).

1. Reperage : on cherche sur toute la fenetre du jeu des pseudos alignes a gauche, les uns sous les
   autres. Leur colonne donne la position du chat (on la garde tant qu'on y lit des messages).
2. Lecture du chat toutes les ~1,2 s : lignes regroupees en messages (pseudo + texte, lignes
   coupees, messages du jeu), onglet actif reconnu par son icone.
3. Les nouveaux messages sont ajoutes a l'historique du canal puis traduits (Google, gratuit)
   vers la langue de l'app.
"""
import difflib
import re
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageChops

import reliques_overlay as ro

BLANC = (239, 239, 239)       # texte des messages
PSEUDO = (42, 170, 140)       # pseudos
PAUSE = 1.2                   # secondes entre deux lectures
MAX_MESSAGES = 300            # par canal

# Empreintes 16 x 16 des icones d'onglets (relevees sur une capture du jeu)
ONGLETS = {
    "Clan": 0x008001e01ffcffffffffffffffffffff7fff7fff3ffe3ffe1ffc1ff80ff007e0,
    "Alliance": 0x081020046004e006c18683c2c7f3d733d80bd80fc00763c632cc381c0c300e70,
    "Aide": 0x0ff87ffefc3ff81ff01f601f007f00fc03f803e003c00180000003c00fc007c0,
    "Recrutement": 0x0ff03878638cc7c6c7c287c383838fc38fe3cfe2e7c6700c1ff80ff80078003c,
    "Commerce": 0x003000fef3fff1f7c033c003c003c003c003c003c003c803cf03ff8fff8f0e00,
}
# langue de l'app -> code Google
LANG_GOOGLE = {"tc": "zh-TW", "sc": "zh-CN", "pt": "pt", "fr": "fr", "en": "en", "de": "de", "es": "es",
               "it": "it", "pl": "pl", "ru": "ru", "ja": "ja", "ko": "ko", "tr": "tr", "uk": "uk", "th": "th"}


def ecart(img, couleur):
    """Image grise : 0 la ou le pixel a exactement cette couleur, plus clair quand il s'en eloigne."""
    d = ImageChops.difference(img, Image.new("RGB", img.size, couleur))
    r, g, b = d.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def filtre(img, couleurs, k):
    """Texte des couleurs voulues en noir sur blanc, agrandi k fois (bien plus lisible pour l'OCR)."""
    d = None
    for c in couleurs:
        e = ecart(img, c)
        d = e if d is None else ImageChops.darker(d, e)
    d = d.point(lambda v: min(255, v * 4))
    return d.resize((d.width * k, d.height * k), Image.LANCZOS) if k > 1 else d


def cle(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def proche(a, b):
    a, b = cle(a), cle(b)
    return a == b or (len(a) > 6 and difflib.SequenceMatcher(None, a, b).ratio() > 0.85)


def nettoie_pseudo(s):
    s = re.sub(r"[\s:;.,'\"]+$", "", s.strip())       # ":" et icone de plateforme lus apres le pseudo
    return re.sub(r"[^\w\-.]+", "", s)


class ChatReader:
    def __init__(self, app):
        self.app = app
        self.enabled = True
        self.statut = "en attente du jeu"
        self.zone = None          # (gauche, haut, droite, bas) du chat dans la fenetre du jeu
        self.ratees = 0
        self.canaux = {}          # canal -> [messages]
        self.messages = []        # tous les messages, dans l'ordre d'arrivee
        self.next_id = 1
        self.lock = threading.Lock()
        self.cache_trad = {}
        self.n = 0

    # ---------------------------------------------------------------- boucle
    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                if self.enabled:
                    self.tour()
                else:
                    self.statut = "desactive"
            except Exception as e:
                self.statut = f"erreur : {e}"
            time.sleep(PAUSE)

    def ocr(self, img):
        self.n += 1
        p = Path(tempfile.gettempdir()) / f"lotus_chat{self.n % 2}.bmp"
        img.save(p)
        return ro.ocr(p, "fr-FR" if self.app.CURRENT_LANG[0] == "fr" else "en-US")

    def tour(self):
        from PIL import ImageGrab
        rect = ro.find_game_rect()
        if not rect:
            self.statut, self.zone = "jeu fermé", None
            time.sleep(2)
            return
        shot = ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
        if not self.zone:
            self.zone = self.reperer(shot)
            if not self.zone:
                self.statut = "chat non visible (ouvre-le dans le jeu)"
                time.sleep(2)  # recherche plus espacee : moins de processeur pendant le jeu
                return
        canal, msgs = self.lire(shot.crop(self.zone))
        if not msgs:
            self.ratees += 1
            if self.ratees >= 4:  # chat ferme ou deplace : on le recherchera
                self.zone, self.statut = None, "chat non visible (ouvre-le dans le jeu)"
            return
        self.ratees = 0
        self.statut = f"lecture du chat ({canal})"
        self.ajouter(canal, msgs)

    # ---------------------------------------------------------------- reperage du chat
    def reperer(self, shot):
        """Colonne de pseudos alignes a gauche = le chat. Renvoie sa zone dans la capture."""
        W, H = shot.size
        lignes = [ln for ln in self.ocr(filtre(shot, [PSEUDO], 1))
                  if len(cle(ln["t"])) >= 3 and re.match(r"^[\w\-.]{3,}", ln["t"].strip())]
        if len(lignes) < 2:
            return None
        textes = self.ocr(filtre(shot, [BLANC], 1))

        def a_un_message(n):  # un vrai pseudo du chat est suivi d'un texte blanc sur la meme ligne
            cy = n["y"] + n["h"] / 2
            return any(abs(t["y"] + t["h"] / 2 - cy) < n["h"] * 0.6
                       and n["x"] + n["w"] - n["h"] < t["x"] < n["x"] + n["w"] + n["h"] * 4
                       for t in textes)
        lignes = [ln for ln in lignes if a_un_message(ln)]
        best = []
        for ln in lignes:  # pseudos dont le bord gauche est au meme endroit
            groupe = [m for m in lignes if abs(m["x"] - ln["x"]) < 8]
            if len(groupe) > len(best):
                best = groupe
        if len(best) < 2:
            return None
        h = sorted(m["h"] for m in best)[len(best) // 2]
        x = min(m["x"] for m in best)
        y1, y2 = min(m["y"] for m in best), max(m["y"] + m["h"] for m in best)
        # le chat fait environ un tiers de l'ecran de large ; onglets ~4,5 lignes au-dessus
        return (max(0, int(x - h * 0.6)), max(0, int(y1 - h * 6)),
                min(W, int(x + W * 0.36)), min(H, int(y2 + h * 6)))

    # ---------------------------------------------------------------- lecture
    def lire(self, img):
        k = 2
        noms = [ln for ln in self.ocr(filtre(img, [PSEUDO], k)) if len(cle(ln["t"])) >= 2]
        textes = self.ocr(filtre(img, [BLANC], k))
        for ln in noms + textes:
            for c in ("x", "y", "w", "h"):
                ln[c] /= k
        if not noms and not textes:
            return None, []
        h = sorted(ln["h"] for ln in noms + textes)[len(noms + textes) // 2]
        gauche = min([ln["x"] for ln in noms] or [ln["x"] for ln in textes])
        # lignes regroupees par hauteur (pseudo et texte d'une meme ligne)
        rangs = []
        for ln in sorted(noms + textes, key=lambda l: l["y"] + l["h"] / 2):
            cy = ln["y"] + ln["h"] / 2
            if rangs and abs(rangs[-1]["cy"] - cy) < h * 0.6:
                rangs[-1]["l"].append(ln)
            else:
                rangs.append({"cy": cy, "l": [ln]})
        haut_texte = min(r["cy"] for r in rangs)
        droite_max = max(ln["x"] + ln["w"] for ln in noms + textes)
        msgs = []
        for r in rangs:
            pseudo = next((ln for ln in sorted(r["l"], key=lambda l: l["x"])
                           if ln in noms and ln["x"] < gauche + h * 1.5), None)
            corps = " ".join(ln["t"] for ln in sorted(r["l"], key=lambda l: l["x"]) if ln is not pseudo)
            corps = re.sub(r"^[\s:;.,'\"•·-]+", "", corps).strip()
            fin = max(ln["x"] + ln["w"] for ln in r["l"])
            if pseudo:
                msgs.append({"auteur": nettoie_pseudo(pseudo["t"]), "texte": corps, "fin": fin})
            elif msgs and msgs[-1]["fin"] > gauche + (droite_max - gauche) * 0.85:
                msgs[-1]["texte"] += " " + corps     # suite d'une ligne coupee
                msgs[-1]["fin"] = fin
            elif corps:
                msgs.append({"auteur": None, "texte": corps, "fin": fin})  # message du jeu
        # un pseudo sans texte ou un bout de texte isole n'est pas un message (decor du jeu lu par erreur)
        msgs = [m for m in msgs if m["texte"] and (m["auteur"] and len(m["auteur"]) >= 3 or len(m["texte"]) >= 8)]
        if not any(m["auteur"] for m in msgs):
            return None, []
        return self.onglet(img, haut_texte - h), msgs

    def onglet(self, img, bas):
        """Onglet actif (fond blanc) au-dessus des messages, reconnu par son icone."""
        band = img.crop((0, 0, img.width, max(1, int(bas)))).convert("L")
        clair = band.point(lambda v: 255 if v > 215 else 0)
        W, H = clair.size
        px = clair.load()
        lignes = [y for y in range(H) if sum(1 for x in range(0, W, 2) if px[x, y]) > 8]
        if not lignes:
            return "Autre"
        # plus long bloc de lignes claires consecutives = la case (pas la loupe ni le soulignement)
        y1, y2, debut = lignes[0], lignes[0], lignes[0]
        for a, b in zip(lignes, lignes[1:] + [None]):
            if b != a + 1:
                if a - debut > y2 - y1:
                    y1, y2 = debut, a
                debut = b
        hauteur = y2 - y1 + 1
        cols = [x for x in range(W) if sum(1 for y in range(y1, y2 + 1, 2) if px[x, y]) > hauteur * 0.25]
        if not cols:
            return "Autre"
        # premiere suite de colonnes claires = la case de l'onglet actif
        x1 = x2 = cols[0]
        for x in cols[1:]:
            if x - x2 > hauteur * 0.7:  # l'icone sombre au milieu coupe la case
                break
            x2 = x
        case = band.crop((x1 + 4, y1 + 3, x2 - 3, y2 - 2)).point(lambda v: 255 if v < 128 else 0)
        bb = case.getbbox()
        if not bb:
            return "Autre"
        icone = case.crop(bb).resize((16, 16), Image.BILINEAR).point(lambda v: 1 if v > 80 else 0)
        bits = int("".join(str(v) for v in icone.tobytes()), 2)
        meilleur, score = "Autre", 0
        for nom, ref in ONGLETS.items():
            s = 256 - bin(bits ^ ref).count("1")
            if s > score:
                meilleur, score = nom, s
        return meilleur if score / 256 > 0.75 else "Autre"

    # ---------------------------------------------------------------- historique
    def ajouter(self, canal, visibles):
        with self.lock:
            hist = self.canaux.setdefault(canal, [])
            # partie deja connue : la fin de l'historique = le debut de ce qu'on voit
            deja = 0
            for k in range(min(len(hist), len(visibles)), 0, -1):
                if all(proche((a["auteur"] or "") + a["texte"], (b["auteur"] or "") + b["texte"])
                       for a, b in zip(hist[-k:], visibles[:k])):
                    deja = k
                    break
            if deja == 0 and hist:  # rien en commun : on evite de recopier un message deja vu
                vus = {cle((m["auteur"] or "") + m["texte"]) for m in hist[-60:]}
                visibles = [v for v in visibles if cle((v["auteur"] or "") + v["texte"]) not in vus]
            nouveaux = []
            for v in visibles[deja:]:
                m = {"id": self.next_id, "canal": canal, "auteur": v["auteur"], "texte": v["texte"],
                     "t": time.time(), "trad": None, "langue": None}
                self.next_id += 1
                hist.append(m)
                self.messages.append(m)
                nouveaux.append(m)
            del hist[:-MAX_MESSAGES]
            del self.messages[:-MAX_MESSAGES * 3]
        for m in nouveaux:
            threading.Thread(target=self.traduire, args=(m,), daemon=True).start()

    def traduire(self, m):
        cible = LANG_GOOGLE.get(self.app.CURRENT_LANG[0], "en")
        k = (cible, m["texte"])
        if k not in self.cache_trad:
            get = self.app.requests.get
            try:  # Google (adresse de l'extension Chrome) : [[traduction, langue detectee]]
                r = get("https://clients5.google.com/translate_a/t", timeout=8,
                        params={"client": "dict-chrome-ex", "sl": "auto", "tl": cible, "q": m["texte"]})
                trad, src = r.json()[0][:2]
                self.cache_trad[k] = (trad, src)
            except Exception:
                try:  # secours : autre adresse Google (parfois limitee)
                    r = get("https://translate.googleapis.com/translate_a/single", timeout=8,
                            params={"client": "gtx", "sl": "auto", "tl": cible, "dt": "t", "q": m["texte"]})
                    data = r.json()
                    self.cache_trad[k] = ("".join(s[0] for s in data[0] if s and s[0]), data[2])
                except Exception:
                    return
        trad, src = self.cache_trad[k]
        m["langue"] = src
        if src != cible.split("-")[0] and cle(trad) != cle(m["texte"]):
            m["trad"] = trad
        m["maj"] = time.time()

    def depuis(self, ident, maj=0):
        """Messages plus recents que ident (ou dont la traduction est arrivee depuis maj)."""
        with self.lock:
            return [m for m in self.messages if m["id"] > ident or m.get("maj", 0) > maj]
