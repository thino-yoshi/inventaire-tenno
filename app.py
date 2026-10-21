"""
App locale : inventaire Warframe (Prime, sets, arcanes, mods, reliques) + prix Warframe Market.

    py app.py                    -> ouvre http://localhost:8765 dans le navigateur
    py app.py --port 9000        -> autre port
    py app.py --sans-navigateur  -> n'ouvre pas le navigateur

La lecture du jeu scanne la memoire de Warframe en lecture seule (comme AlecaFrame/Overwolf).
Sans le jeu, l'app relit la derniere copie (inventory_dump.json).
"""
import argparse
import json
import os
import re
import subprocess
import socket
import statistics
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

RELAUNCH_FLAG = "INVENTAIRE_RELANCE"


def clean_env():
    """Environnement pour lancer un autre exe de l'app : sans les variables internes de
    PyInstaller, sinon le nouvel exe reutilise le dossier temporaire de l'ancien (efface a sa
    fermeture) et plante faute de fichiers."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env[RELAUNCH_FLAG] = "1"
    return env


# Lance par une ancienne version (<= 1.0.4) apres une mise a jour, avec son environnement :
# on se relance tout de suite proprement, avant que l'ancienne efface son dossier temporaire.
if getattr(sys, "frozen", False) and "--attendre-pid" in sys.argv and not os.environ.get(RELAUNCH_FLAG):
    subprocess.Popen([sys.executable] + sys.argv[1:], env=clean_env(),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    os._exit(0)

import requests

import prime_inventory as pi

APP_NAME = "Lotus Desk"
VERSION = "1.12.1"
UPDATE_REPO = "thino-yoshi/inventaire-tenno"   # depot GitHub ou sont publiees les versions
SRC_ASSET = "inventaire-tenno-src.zip"           # zip du code dans chaque release
EXE_ASSET = "LotusDesk.exe"          # l'exe de chaque release
OLD_EXE_ASSET = "InventaireWarframe.exe"  # ancien nom, encore publie le temps que tout le monde passe en 1.9.1+

FROZEN = getattr(sys, "frozen", False)
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", BASE))     # fichiers embarques dans le .exe
DATA_DIR = BASE / "wf_data"
DUMP = BASE / "inventory_dump.json"
HISTORY = BASE / "historique.json"
PRICE_CACHE = BASE / "cache_prix.json"
SET_CACHE = BASE / "cache_sets.json"
CATALOG_CACHE = BASE / "cache_wfm_items.json"

PRICE_TTL = 30 * 60          # prix gardes 30 min
CATALOG_TTL = 24 * 3600      # liste des items WFM gardee 1 jour
DATA_TTL = 7 * 24 * 3600     # fichiers du jeu re-telecharges au bout de 7 jours
TOP_N = 10
WFM_SPACING = 0.35           # ~3 requetes/s maximum
LANGS = ["fr", "en", "de", "es", "it", "pt", "pl", "ru", "uk", "tr", "ja", "ko", "zh", "tc", "th"]
EXPORTS = {"resources": "ExportResources.json", "recipes": "ExportRecipes.json",
           "warframes": "ExportWarframes.json", "weapons": "ExportWeapons.json",
           "sentinels": "ExportSentinels.json", "upgrades": "ExportUpgrades.json",
           "arcanes": "ExportArcanes.json", "relics": "ExportRelics.json"}
RELIC_QUALITY = {"VPQ_BRONZE": ("Bronze", "intact"), "VPQ_SILVER": ("Silver", "exceptional"),
                 "VPQ_GOLD": ("Gold", "flawless"), "VPQ_PLATINUM": ("Platinum", "radiant")}
CAT_ORDER = {"prime": 0, "arcane": 1, "relique": 2, "mod": 3, "objet": 4, "ressource": 5}

_lock = threading.Lock()


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ================================================================ donnees du jeu
def fetch_data_file(fn, force=False):
    path = DATA_DIR / fn
    if path.exists() and not force and time.time() - path.stat().st_mtime < DATA_TTL:
        return path
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        print(f"Telechargement de {fn}...")
        r = requests.get(pi.DATA_URL + fn, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
    except requests.RequestException as e:
        if not path.exists():
            raise RuntimeError(f"Impossible de telecharger {fn} : {e}")
        print(f"  echec ({e}) : copie locale utilisee.")
    return path


class GameData:
    def __init__(self):
        self.ex = {k: load_json(fetch_data_file(fn)) for k, fn in EXPORTS.items()}
        self.sections = [self.ex[k] for k in ("resources", "warframes", "weapons", "sentinels",
                                               "upgrades", "arcanes")]
        self.dicts = {}

    def tr(self, lang):
        if lang not in self.dicts:
            self.dicts[lang] = load_json(fetch_data_file(f"dict.{lang}.json"))
        return self.dicts[lang]

    def name(self, path, lang, depth=0):
        tr = self.tr(lang)
        if depth > 3:
            return None
        rel = self.ex["relics"].get(path)
        if rel:
            tmpl = tr.get("/Lotus/Language/Relics/VoidProjectionName", "|ERA| |CATEGORY|")
            era = tr.get("/Lotus/Language/Relics/Era_" + rel["era"].upper(), rel["era"])
            return tmpl.replace("|ERA|", era).replace("|CATEGORY|", rel["category"])
        for sect in self.sections:
            e = sect.get(path)
            if e and e.get("name"):
                return tr.get(e["name"])
        rec = self.ex["recipes"].get(path)
        if rec and rec.get("resultType"):
            inner = self.name(rec["resultType"], lang, depth + 1)
            tmpl = tr.get("/Lotus/Language/Items/BlueprintAndItem")
            if inner and tmpl:
                return tmpl.replace("|ITEM|", inner)
        return None

    def rewards(self):
        """Tables de recompenses (reliques), chargees seulement si on en a besoin."""
        if not hasattr(self, "_rewards"):
            self._rewards = load_json(fetch_data_file("ExportRewards.json"))
        return self._rewards

    def icon_url(self, path, depth=0):
        """Icone officielle du jeu (servie par browse.wf), pour les items absents de WFM."""
        if depth > 3:
            return None
        for sect in self.sections:
            e = sect.get(path)
            if e and e.get("icon"):
                return "https://browse.wf" + e["icon"]
        rec = self.ex["recipes"].get(path)
        if rec and rec.get("resultType"):
            return self.icon_url(rec["resultType"], depth + 1)
        return None

    def relic_quality(self, path, lang):
        rel = self.ex["relics"].get(path)
        if not rel or rel.get("quality") not in RELIC_QUALITY:
            return None, None
        word, sub = RELIC_QUALITY[rel["quality"]]
        q = self.tr(lang).get(f"/Lotus/Language/Relics/VoidProjectionQuality_{word}", sub)
        return q.capitalize(), sub


_game = None


def game():
    global _game
    if _game is None:
        _game = GameData()
    return _game


# ================================================================ Warframe Market
_next_slot = 0.0
_slot_lock = threading.Lock()


def wfm_get(url, tries=3):
    """Requete WFM ; les departs sont espaces de WFM_SPACING pour respecter la limite."""
    global _next_slot
    for attempt in range(tries):
        with _slot_lock:
            start = max(time.time(), _next_slot)
            _next_slot = start + WFM_SPACING
        time.sleep(max(0.0, start - time.time()))
        r = requests.get(url, headers=pi.HEADERS, timeout=30)
        if r.status_code == 429 and attempt < tries - 1:
            time.sleep(2)
            continue
        r.raise_for_status()
        return r.json()


class Catalog:
    def __init__(self):
        cached = load_json(CATALOG_CACHE)
        if not cached or time.time() - cached.get("t", 0) > CATALOG_TTL:
            try:
                cached = {"t": time.time(), "items": wfm_get(f"{pi.WFM}/items")["data"]}
                save_json(CATALOG_CACHE, cached)
            except requests.RequestException:
                if not cached:
                    raise
        self.by_slug, self.by_ref, self.by_name, self.by_id = {}, {}, {}, {}
        for it in cached["items"]:
            self.by_slug[it["slug"]] = it
            self.by_id[it["id"]] = it["slug"]
            en = (it.get("i18n", {}).get("en", {}) or {}).get("name")
            if en:
                self.by_name[pi.norm(en)] = it["slug"]
            if it.get("gameRef"):
                self.by_ref[it["gameRef"]] = it["slug"]

    def find(self, path, en_name):
        if path in self.by_ref:
            return self.by_ref[path]
        if en_name:
            if pi.norm(en_name) in self.by_name:
                return self.by_name[pi.norm(en_name)]
            guess = re.sub(r"[^a-z0-9]+", "_", en_name.lower().replace("&", "and")).strip("_")
            if guess in self.by_slug:
                return guess
        return None

    def rarity(self, slug):
        """Rarete pour le cadre des cartes : prime, legendary, rare, uncommon, common."""
        tags = self.by_slug.get(slug, {}).get("tags", []) if slug else []
        if "prime" in tags:
            return "prime"
        return next((r for r in ("legendary", "rare", "uncommon", "common") if r in tags), "common")

    def images(self, slug):
        """(vignette 128px, grande image) de l'item, chemins relatifs aux assets de WFM."""
        meta = self.by_slug.get(slug, {}) if slug else {}
        tags = meta.get("tags") or []
        # pieces de Warframe (neuroptiques, chassis, systemes) : WFM reprend l'image de la Warframe
        # entiere, qu'on confondrait avec son schema ; on prend l'icone de la piece du jeu
        if "component" in tags and "weapon" not in tags and meta.get("gameRef"):
            try:
                ic = game().icon_url(meta["gameRef"])
                if ic:
                    return ic, ic
            except Exception:
                pass
        en = (meta.get("i18n", {}).get("en", {}) or {}) if slug else {}
        return en.get("thumb"), en.get("icon")

    def en(self, slug):
        it = self.by_slug.get(slug, {})
        return (it.get("i18n", {}).get("en", {}) or {}).get("name", slug)


_catalog = None


def catalog():
    global _catalog
    if _catalog is None:
        _catalog = Catalog()
    return _catalog


_prices = load_json(PRICE_CACHE, {}) or {}
_prices_saved = 0.0


# ---------------------------------------------------------------- prix partages
# Une GitHub Action releve tous les prix toutes les 6 h (tools/snapshot_prix.py). L'app telecharge
# ce fichier au demarrage : tous les prix s'affichent tout de suite, meme au premier lancement.
SHARED_URL = f"https://github.com/{UPDATE_REPO}/releases/download/prix/prix.json.gz"
SHARED_FILE = BASE / "prix_partages.json.gz"
SHARED_MAX_AGE = 3 * 3600
_shared = {"t": 0, "items": {}}
_shared_ready = threading.Event()


def load_shared():
    """Telecharge le fichier de prix s'il est vieux de plus de 3 h, puis le charge en memoire."""
    import gzip
    try:
        fresh = SHARED_FILE.exists() and time.time() - SHARED_FILE.stat().st_mtime < SHARED_MAX_AGE
        if not fresh:
            r = requests.get(SHARED_URL, timeout=30)
            if r.ok:
                SHARED_FILE.write_bytes(r.content)
        if SHARED_FILE.exists():
            data = json.loads(gzip.decompress(SHARED_FILE.read_bytes()))
            _shared.update(t=data.get("t", 0), items=data.get("items", {}))
    except Exception as e:  # pas grave : on retombe sur les requetes item par item
        print("prix partages :", e)
    finally:
        _shared_ready.set()


def shared_loop():
    while True:
        load_shared()
        time.sleep(SHARED_MAX_AGE)


def shared_prices(slug, rank=None, subtype=None):
    """Meme calcul que prices(), a partir du releve partage. None si l'item n'y est pas."""
    groups = _shared["items"].get(slug)
    if not groups:
        return None
    parsed = []
    for key, (n_off, n_onl, top) in groups.items():
        r, st = key.split("|")
        parsed.append((None if r == "None" else int(r), None if st == "None" else st, n_off, n_onl, top))
    if subtype:
        parsed = [g for g in parsed if g[1] in (subtype, None)]
    note = ""
    if rank is not None:
        exact = [g for g in parsed if g[0] == rank]
        if exact:
            parsed = exact
        else:
            zero = [g for g in parsed if (g[0] or 0) == 0]
            parsed, note = (zero, "rang0") if zero else (parsed, "tous_rangs")
    me = ((ACCOUNT.user or {}).get("pseudo") if ACCOUNT else None)
    offers = sorted((o for g in parsed for o in g[4] if o[2] != me), key=lambda o: o[0])
    top = [{"prix": o[0], "rang": o[1], "vendeur": o[2], "statut": o[3], "qte": o[4], "rep": o[5]}
           for o in offers[:TOP_N]]
    return {"t": _shared["t"], "top": top, "min": top[0]["prix"] if top else None,
            "median": round(statistics.median(t["prix"] for t in top)) if top else None,
            "nb_offres": sum(g[2] for g in parsed), "nb_en_ligne": sum(g[3] for g in parsed),
            "note": note, "partage": True}


def prices(slug, rank=None, subtype=None, force=False, cached_ok=False):
    """cached_ok : renvoie le dernier prix connu meme ancien (l'interface le rafraichit ensuite)."""
    key = f"{slug}|{rank}|{subtype}"
    hit = _prices.get(key)
    if hit and not force and (cached_ok or time.time() - hit["t"] < PRICE_TTL):
        if not (cached_ok and _shared["t"] > hit["t"]):
            return hit
    if cached_ok and not force:
        _shared_ready.wait(15)  # au demarrage, le fichier partage arrive en 1-2 s
        shared = shared_prices(slug, rank, subtype)
        if shared and (not hit or shared["t"] > hit["t"]):
            return shared
        if hit:
            return hit
    orders = wfm_get(f"{pi.WFM}/orders/item/{slug}")["data"]
    me = ((ACCOUNT.user or {}).get("pseudo") if ACCOUNT else None)
    # mes propres offres ne comptent pas dans le prix du marche (sinon le min serait mon prix)
    sells = [o for o in orders if o["type"] == "sell" and o.get("visible", True)
             and o["user"].get("ingameName") != me]
    if subtype:
        sells = [o for o in sells if o.get("subtype") in (subtype, None)]
    note = ""
    if rank is not None:
        exact = [o for o in sells if o.get("rank") == rank]
        if exact:
            sells = exact
        else:
            zero = [o for o in sells if o.get("rank", 0) == 0]
            sells, note = (zero, "rang0") if zero else (sells, "tous_rangs")
    online = [o for o in sells if o["user"].get("status") in ("ingame", "online")]
    pool = sorted(online or sells, key=lambda o: o["platinum"])
    top = [{"prix": o["platinum"], "qte": o["quantity"], "rang": o.get("rank"),
            "vendeur": o["user"]["ingameName"], "statut": o["user"].get("status", "offline"),
            "rep": o["user"].get("reputation", 0)} for o in pool[:TOP_N]]
    res = {"t": time.time(), "top": top,
           "min": top[0]["prix"] if top else None,
           "median": round(statistics.median(t["prix"] for t in top)) if top else None,
           "nb_offres": len(sells), "nb_en_ligne": len(online), "note": note}
    global _prices_saved
    with _lock:
        _prices[key] = res
        if time.time() - _prices_saved > 5:  # pas a chaque prix : le fichier fait vite 1 Mo
            save_json(PRICE_CACHE, _prices)
            _prices_saved = time.time()
    return res


def flush_prices():
    with _lock:
        save_json(PRICE_CACHE, _prices)


_sets = load_json(SET_CACHE, {}) or {}


def set_parts(set_slug):
    """[{slug, ref, qte}] des pieces d'un set (hors l'item set lui-meme), garde en cache."""
    if set_slug not in _sets:
        data = wfm_get(f"{pi.WFM}/item/{set_slug}/set")["data"]["items"]
        _sets[set_slug] = [{"slug": p["slug"], "ref": p.get("gameRef"),
                            "qte": p.get("quantityInSet", 1)}
                           for p in data if not p.get("setRoot")]
        with _lock:
            save_json(SET_CACHE, _sets)
    return _sets[set_slug]


# ================================================================ compte Warframe Market
# Le mot de passe sert uniquement a la connexion et n'est jamais enregistre. Le jeton de session
# est chiffre avec DPAPI (Windows) : seul ce compte Windows peut le relire.
SESSION_FILE = BASE / "session_wfm.dat"
SALES_LOG = BASE / "mes_ventes.json"
WFM_V1 = "https://api.warframe.market/v1"

if pi.IS_WINDOWS:
    import base64
    import ctypes
    import uuid

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32")
    _kernel32 = ctypes.WinDLL("kernel32")
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _dpapi(data, protect):
        buf = ctypes.create_string_buffer(data, len(data))
        src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = _Blob()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
            raise OSError("chiffrement Windows impossible")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class Account:
    def __init__(self):
        self.token = None
        self.device = None
        self.user = None
        self.orders = []
        try:
            raw = json.loads(_dpapi(base64.b64decode(SESSION_FILE.read_bytes()), False))
            self.token, self.device = raw.get("token"), raw.get("device")
        except Exception:
            pass
        self.device = self.device or str(uuid.uuid4())

    def _save(self):
        if self.token:
            blob = json.dumps({"token": self.token, "device": self.device}).encode()
            SESSION_FILE.write_bytes(base64.b64encode(_dpapi(blob, True)))
        else:
            SESSION_FILE.unlink(missing_ok=True)

    def headers(self):
        return {**pi.HEADERS, "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def call(self, method, path, body=None):
        """Requete authentifiee vers l'API v2. Deconnecte si le jeton est refuse."""
        if not self.token:
            raise RuntimeError("non_connecte")
        global _next_slot
        with _slot_lock:
            start = max(time.time(), _next_slot)
            _next_slot = start + WFM_SPACING
        time.sleep(max(0.0, start - time.time()))
        r = requests.request(method, f"{pi.WFM}{path}", headers=self.headers(), json=body, timeout=30)
        if r.status_code == 401:
            self.logout()
            raise RuntimeError("session_expiree")
        if not r.ok:
            raise RuntimeError(wfm_error(r))
        return r.json() if r.content else {}

    def login(self, email, password):
        r = requests.post(f"{WFM_V1}/auth/signin", timeout=30,
                          headers={**pi.HEADERS, "Authorization": "JWT", "Content-Type": "application/json"},
                          json={"auth_type": "header", "email": email, "password": password,
                                "device_id": self.device})
        if not r.ok:
            raise RuntimeError("identifiants_refuses" if r.status_code in (400, 401, 403)
                               else wfm_error(r))
        auth = r.headers.get("Authorization", "")
        if not auth.startswith("JWT "):
            raise RuntimeError("connexion_impossible")
        self.token = auth[4:]
        self._save()
        return self.refresh()

    def logout(self):
        self.token, self.user, self.orders = None, None, []
        self._save()

    def refresh(self):
        """Recharge le profil et les offres ; retourne l'etat du compte."""
        if self.token:
            me = self.call("GET", "/me").get("data") or {}
            self.user = {"pseudo": me.get("ingameName"), "slug": me.get("slug"),
                         "statut": me.get("status")}
            self.load_orders()
        return self.state()

    def load_orders(self):
        cat = catalog()
        out = []
        for o in self.call("GET", "/orders/my").get("data") or []:
            slug = cat.by_id.get(o["itemId"])
            ref = cat.by_slug.get(slug, {}).get("gameRef") if slug else None
            out.append({"id": o["id"], "type": o["type"], "slug": slug,
                        "nom_en": cat.en(slug) if slug else None,
                        "vignette": cat.images(slug)[0], "image": cat.images(slug)[1],
                        "rarete": cat.rarity(slug),
                        "nom": (game().name(ref, CURRENT_LANG[0]) if ref else None)
                               or (cat.en(slug) if slug else None),
                        "prix": o["platinum"], "qte": o["quantity"], "rang": o.get("rank"),
                        "sous_type": o.get("subtype"), "visible": o.get("visible", True)})
        self.orders = out
        return out

    def state(self):
        return {"connecte": bool(self.token), "compte": self.user, "offres": self.orders}

    def sell(self, slug, platinum, quantity, rank, subtype, visible):
        meta = catalog().by_slug.get(slug)
        if not meta:
            raise RuntimeError("item_inconnu")
        body = {"itemId": meta["id"], "type": "sell", "platinum": int(platinum),
                "quantity": int(quantity), "visible": bool(visible)}
        if rank is not None and meta.get("maxRank") is not None:
            body["rank"] = int(rank)
        if subtype:
            body["subtype"] = subtype
        self.call("POST", "/order", body)
        self.load_orders()
        return self.state()

    def update(self, order_id, platinum, quantity, visible):
        body = {"platinum": int(platinum), "quantity": int(quantity), "visible": bool(visible)}
        self.call("PATCH", f"/order/{order_id}", body)
        self.load_orders()
        return self.state()

    def close(self, order_id, quantity):
        """Marque l'offre comme vendue : WFM l'enregistre dans ses statistiques de ventes."""
        o = next((x for x in self.orders if x["id"] == order_id), None)
        qty = max(1, int(quantity))
        self.call("POST", f"/order/{order_id}/close", {"quantity": qty})
        if o:  # journal local de mes ventes
            log = load_json(SALES_LOG, []) or []
            log.append({"t": int(time.time()), "slug": o["slug"], "nom": o.get("nom"),
                        "prix": o["prix"], "qte": qty, "rang": o.get("rang")})
            save_json(SALES_LOG, log[-5000:])
        self.load_orders()
        return self.state()

    def delete(self, order_id):
        self.call("DELETE", f"/order/{order_id}")
        self.load_orders()
        return self.state()


def wfm_error(r):
    try:
        err = r.json().get("error")
        if isinstance(err, dict):
            return "WFM : " + "; ".join(f"{k} {v}" for k, v in err.items())
        if err:
            return f"WFM : {err}"
    except ValueError:
        pass
    return f"WFM : erreur {r.status_code}"


    def sell_many(self, lots):
        """Plusieurs offres d'un coup ; la liste des offres n'est rechargee qu'a la fin."""
        cat, erreurs, faites = catalog(), [], 0
        for lot in lots:
            meta = cat.by_slug.get(lot.get("slug"))
            if not meta:
                continue
            body = {"itemId": meta["id"], "type": "sell", "platinum": int(lot["prix"]),
                    "quantity": int(lot["qte"]), "visible": True}
            if lot.get("rang") is not None and meta.get("maxRank") is not None:
                body["rank"] = int(lot["rang"])
            if lot.get("sous_type"):
                body["subtype"] = lot["sous_type"]
            try:
                self.call("POST", "/order", body)
                faites += 1
            except RuntimeError as e:
                if str(e) in ("session_expiree", "non_connecte"):
                    raise
                erreurs.append(f"{cat.en(lot['slug'])} : {e}")
        self.load_orders()
        return {**self.state(), "faites": faites, "erreurs": erreurs}


ACCOUNT = None
CURRENT_LANG = ["fr"]  # langue de l'interface, pour nommer les offres


def account():
    global ACCOUNT
    if ACCOUNT is None:
        ACCOUNT = Account()
    return ACCOUNT


# ================================================================ statut "en jeu" sur WFM
# Les acheteurs voient d'abord les vendeurs "en jeu". Tant que Warframe tourne, on garde une
# connexion websocket ouverte avec ce statut ; a la fermeture du jeu on repasse "invisible".
WS_URL = "wss://ws.warframe.market/socket"


class Presence:
    def __init__(self):
        self.auto = True
        self.ws = None
        self.statut = "hors_ligne"
        self.lock = threading.Lock()

    def _send(self, route, payload=None):
        msg = {"route": route, "id": uuid.uuid4().hex[:12]}
        if payload is not None:
            msg["payload"] = payload
        self.ws.send(json.dumps(msg))

    def _open(self, token):
        import websocket  # websocket-client
        ws = websocket.WebSocketApp(
            WS_URL, subprotocols=["wfm"], header={"User-Agent": "lotus-desk"},
            # le statut n'est accepte qu'une fois l'authentification confirmee (signIn:ok)
            on_open=lambda w: self._send("@wfm|cmd/auth/signIn", {"token": token}),
            on_message=self._on_message,
            on_close=lambda *a: self._closed())
        self.ws = ws
        threading.Thread(target=lambda: ws.run_forever(ping_interval=30, ping_timeout=10),
                         daemon=True).start()

    def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        route = msg.get("route", "")
        if route.endswith("signIn:ok"):
            self._send("@wfm|cmd/status/set", {"status": "ingame"})
        elif route.endswith("status/set") or route.endswith("status/set:ok"):
            self.statut = (msg.get("payload") or {}).get("status", self.statut)
        elif route.endswith("signIn:error"):
            self.statut = "erreur"

    def _closed(self):
        self.ws = None
        if self.statut != "erreur":
            self.statut = "hors_ligne"

    def tick(self):
        """Appele regulierement : ouvre ou ferme la connexion selon l'etat du jeu."""
        acc = account()
        want = self.auto and bool(acc.token) and STATE.game_running
        with self.lock:
            if want and self.ws is None:
                self.statut = "connexion"
                self._open(acc.token)
            elif not want and self.ws is not None:
                try:
                    self._send("@wfm|cmd/status/set", {"status": "invisible"})
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None
                self.statut = "hors_ligne"


PRESENCE = Presence()


# ================================================================ veille : alertes de prix
# Toutes les 3 min : mes offres depassees par un vendeur moins cher, et les items de ma liste
# de souhaits qui passent sous mon prix maximum.
WISHLIST = BASE / "souhaits.json"
_alerts = {}          # cle -> alerte
_notified = set()     # cles deja notifiees (une notification par alerte)
_notify = None        # fonction de notification (icone de la barre des taches)


def others_min(p, me, rank=None):
    """Offre la moins chere des autres vendeurs, au meme rang (un mod R7 ne se compare pas a un R0)."""
    for o in p["top"]:
        if o["vendeur"] != me and (rank is None or o.get("rang") in (None, rank)):
            return o
    return None


def watch_once():
    acc, cat = account(), catalog()
    found = {}
    me = (acc.user or {}).get("pseudo")
    if acc.token:
        try:
            acc.load_orders()
        except Exception:
            pass
        for o in acc.orders:
            if o["type"] != "sell" or not o["slug"] or not o["visible"]:
                continue
            try:
                p = prices(o["slug"], o["rang"], o["sous_type"], force=True)
            except Exception:
                continue
            best = others_min(p, me, o["rang"])
            if best and best["prix"] < o["prix"]:
                k = f"depasse|{o['id']}|{best['prix']}"
                found[k] = {"cle": k, "type": "depasse", "offre": o["id"], "slug": o["slug"],
                            "nom_en": cat.en(o["slug"]), "rang": o["rang"],
                            "mon_prix": o["prix"], "leur_prix": best["prix"],
                            "vendeur": best["vendeur"]}
    for w in load_json(WISHLIST, []) or []:
        try:
            p = prices(w["slug"], w.get("rang"), None, force=True)
        except Exception:
            continue
        best = others_min(p, me, w.get("rang"))
        if best and best["prix"] <= w["prix_max"]:
            k = f"souhait|{w['slug']}|{w.get('rang')}|{best['prix']}"
            found[k] = {"cle": k, "type": "souhait", "slug": w["slug"], "nom_en": cat.en(w["slug"]),
                        "rang": w.get("rang"), "prix": best["prix"], "prix_max": w["prix_max"],
                        "vendeur": best["vendeur"]}
    _alerts.clear()
    _alerts.update(found)
    for k, a in found.items():
        if k not in _notified and _notify:
            _notified.add(k)
            if a["type"] == "depasse":
                _notify(f"{a['nom_en']} : {a['vendeur']} vend a {a['leur_prix']}p (toi {a['mon_prix']}p)",
                        "Offre depassee")
            else:
                _notify(f"{a['nom_en']} a {a['prix']}p chez {a['vendeur']} (max {a['prix_max']}p)",
                        "Liste de souhaits")


_watch_now = threading.Event()


def watch_loop():
    while True:
        try:
            watch_once()
        except Exception as e:
            print("veille :", e)
        _watch_now.wait(180)
        _watch_now.clear()


def presence_loop():
    while True:
        try:
            PRESENCE.tick()
        except Exception as e:
            print("statut WFM :", e)
        time.sleep(15)


def wishlist_set(slug, rank, prix_max, remove=False):
    h = [w for w in (load_json(WISHLIST, []) or [])
         if not (w["slug"] == slug and w.get("rang") == rank)]
    if not remove:
        h.append({"slug": slug, "rang": rank, "prix_max": int(prix_max)})
    save_json(WISHLIST, h)
    _watch_now.set()
    return wishlist_view()


def wishlist_view(lang="fr"):
    cat, g = catalog(), game()
    out = []
    for w in load_json(WISHLIST, []) or []:
        meta = cat.by_slug.get(w["slug"], {})
        ref = meta.get("gameRef")
        out.append({**w, "nom_en": cat.en(w["slug"]), "rang_max": meta.get("maxRank"),
                    "vignette": cat.images(w["slug"])[0], "image": cat.images(w["slug"])[1],
                    "rarete": cat.rarity(w["slug"]),
                    "nom": (g.name(ref, lang) if ref else None) or cat.en(w["slug"])})
    return out


def catalog_view(lang):
    cat, g = catalog(), game()
    out = []
    for slug, it in cat.by_slug.items():
        en = cat.en(slug)
        ref = it.get("gameRef")
        nom = (g.name(ref, lang) if ref and lang != "en" else None) or en
        out.append({"slug": slug, "nom": nom, "nom_en": en, "rang_max": it.get("maxRank"),
                    "img": cat.images(slug)[0], "ducats": it.get("ducats")})
    out.sort(key=lambda x: x["nom"])
    return out


# ================================================================ reliques et volumes
RELIC_CHANCES = {  # chance par objet : commun (x3), peu commun (x2), rare (x1)
    "intact": {"COMMON": .2533, "UNCOMMON": .11, "RARE": .02},
    "exceptional": {"COMMON": .2333, "UNCOMMON": .13, "RARE": .04},
    "flawless": {"COMMON": .20, "UNCOMMON": .17, "RARE": .06},
    "radiant": {"COMMON": .1667, "UNCOMMON": .20, "RARE": .10},
}


def relic_value(path, lang):
    g, cat = game(), catalog()
    rel = g.ex["relics"].get(path)
    if not rel:
        raise RuntimeError("item_inconnu")
    pool = (g.rewards().get(rel["rewardManifest"]) or [[]])[0]
    rows = []
    for r in pool:
        ref = r["type"].replace("/StoreItems/", "/", 1)
        en = g.name(ref, "en")
        slug = cat.find(ref, en)
        price = None
        if slug:
            try:
                price = prices(slug)["min"]
            except Exception:
                pass
        rows.append({"nom": g.name(ref, lang) or en or ref.rsplit("/", 1)[-1], "slug": slug,
                     "rarete": r["rarity"], "qte": r.get("itemCount", 1), "prix": price,
                     "ducats": cat.by_slug.get(slug, {}).get("ducats") if slug else None})
    values = {q: round(sum(ch[r["rarete"]] * (r["prix"] or 0) * r["qte"] for r in rows), 1)
              for q, ch in RELIC_CHANCES.items()}
    order = {"RARE": 0, "UNCOMMON": 1, "COMMON": 2}
    rows.sort(key=lambda r: (order.get(r["rarete"], 3), -(r["prix"] or 0)))
    return {"recompenses": rows, "valeurs": values, "chances": RELIC_CHANCES}


_baro = {"t": 0, "data": None}


WORLDSTATE_URL = "https://api.warframe.com/cdn/worldState.php"  # source officielle de DE


def baro_officiel(lang):
    """Visite de Baro lue dans le worldState officiel du jeu ; lieu traduit avec les fichiers du jeu."""
    import datetime
    w = requests.get(WORLDSTATE_URL, timeout=20).json()
    v = (w.get("VoidTraders") or [None])[0]
    if not v:
        return None
    iso = lambda d: datetime.datetime.fromtimestamp(int(d["$date"]["$numberLong"]) / 1000,
                                                    datetime.timezone.utc).isoformat()
    g = game()
    reg = (load_json(fetch_data_file("ExportRegions.json")) or {}).get(v.get("Node"), {})
    tr = g.tr(lang)
    lieu = tr.get(reg.get("name"), v.get("Node"))
    if reg.get("systemName"):
        lieu = f"{lieu} ({tr.get(reg['systemName'], '')})"
    return {"debut": iso(v["Activation"]), "fin": iso(v["Expiry"]), "lieu": lieu}


def baro():
    """Prochaine (ou actuelle) visite de Baro Ki'Teer : source officielle, warframestat.us en secours."""
    if time.time() - _baro["t"] > 1800 or not _baro["data"]:
        try:
            data = baro_officiel(CURRENT_LANG[0])
        except Exception as e:
            print("Baro (source officielle) :", e)
            data = None
        if not data:
            r = requests.get("https://api.warframestat.us/pc/voidTrader?language=fr", timeout=20)
            r.raise_for_status()
            d = r.json()
            data = {"debut": d.get("activation"), "fin": d.get("expiry"), "lieu": d.get("location")}
        _baro.update(t=time.time(), data=data)
    return _baro["data"]


_volumes = {}
_stats = {}


def wfm_stats(slug):
    """Statistiques des ventes conclues sur Warframe Market (48 h et 90 jours), gardees 1 h."""
    hit = _stats.get(slug)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    data = wfm_get(f"{WFM_V1}/items/{slug}/statistics")["payload"]["statistics_closed"]
    _stats[slug] = (time.time(), data)
    return data


def price_history(slug, rank=None):
    """Prix median par jour sur 90 jours : [{d, prix, min, volume}] (onglet Market > Stats/Achats)."""
    data = wfm_stats(slug).get("90days") or []
    rangs = {d["mod_rank"] for d in data if "mod_rank" in d}
    if rangs:  # WFM ne suit que certains rangs (0 et max) : on prend le plus proche
        r = min(rangs, key=lambda x: abs(x - (rank or 0)))
        data = [d for d in data if d.get("mod_rank") == r]
    return [{"d": d["datetime"][:10], "prix": d.get("median") or d.get("avg_price"),
             "min": d.get("min_price"), "volume": d.get("volume", 0)} for d in data]


def volume(slug, rank=None):
    key = f"{slug}|{rank}"
    hit = _volumes.get(key)
    if hit and time.time() - hit["t"] < 3600:
        return hit
    data = wfm_stats(slug)["48hours"]
    if rank is not None and any("mod_rank" in d for d in data):
        data = [d for d in data if d.get("mod_rank") == rank]
    vol = sum(d.get("volume", 0) for d in data)
    avg = round(sum(d.get("avg_price", 0) * d.get("volume", 0) for d in data) / vol) if vol else None
    res = {"t": time.time(), "volume": vol, "prix_moyen": avg}
    _volumes[key] = res
    return res


# ================================================================ inventaire
def classify(path, g):
    if pi.is_prime_part(path):
        return "prime"
    if path in g.ex["relics"]:
        return "relique"
    if path in g.ex["arcanes"]:
        return "arcane"
    if path in g.ex["upgrades"] and "/Randomized/" not in path:
        return "mod"
    # le reste n'est en general pas echangeable, mais on veut savoir ce qu'on possede
    if path.startswith("/Lotus/Types/Recipes/") or path in g.ex["recipes"]:
        return "objet"      # schemas et pieces non Prime (ex. Banshee - Neuroptiques)
    if path in g.ex["resources"]:
        return "ressource"  # Orokin, Plastides, Neurodes...
    return None


def owned_entries(inv, g):
    """{(chemin, rang): quantite} pour tout ce qui peut s'echanger."""
    found = {}

    def add(path, rank, n):
        k = (path, rank)
        found[k] = found.get(k, 0) + n

    for section in ("MiscItems", "Recipes", "RawUpgrades"):
        for it in inv.get(section, []) or []:
            path = it.get("ItemType", "")
            cat = classify(path, g)
            if cat:
                add(path, 0 if cat in ("arcane", "mod") else None, int(it.get("ItemCount", 1)))
    for it in inv.get("Upgrades", []) or []:
        path = it.get("ItemType", "")
        if classify(path, g) in ("arcane", "mod"):
            try:
                lvl = int(json.loads(it.get("UpgradeFingerprint") or "{}").get("lvl", 0))
            except (ValueError, AttributeError):
                lvl = 0
            add(path, lvl, 1)
    return found


def build_items(inv, lang):
    g, cat = game(), catalog()
    items = []
    for (path, rank), qty in owned_entries(inv, g).items():
        kind = classify(path, g)
        en = g.name(path, "en")
        name = g.name(path, lang) or en
        if not name:
            continue
        sub = sub_label = None
        lookup = path
        if kind == "relique":
            sub_label, sub = g.relic_quality(path, lang)
            lookup = re.sub(r"(Bronze|Silver|Gold|Platinum)$", "", path)
        slug = cat.find(lookup, en) or (cat.find(path, en) if lookup != path else None)
        if kind in ("arcane", "mod", "relique") and not slug:
            continue  # pas echangeable
        meta = cat.by_slug.get(slug, {}) if slug else {}
        thumb, big = cat.images(slug)
        if not thumb:  # pas sur WFM : icone officielle du jeu
            thumb = big = g.icon_url(path)
        if kind == "relique" and sub_label:
            name = f"{name} ({sub_label})"
        items.append({
            "id": f"{path}|{rank}", "chemin": path, "cat": kind, "nom": name,
            "nom_en": cat.en(slug) if slug else en, "qte": qty,
            "rang": rank if meta.get("maxRank") else None, "rang_max": meta.get("maxRank"),
            "sous_type": sub, "slug": slug, "ducats": meta.get("ducats"),
            "vignette": thumb, "image": big,
            # reliques : couleur de l'ere (lith, meso, neo, axi, requiem) ; sinon rarete WFM
            "rarete": ("ere-" + g.ex["relics"][path]["era"].lower()) if kind == "relique"
                      else "prime" if kind == "prime"
                      else "neutre" if kind in ("objet", "ressource") and not slug else cat.rarity(slug),
        })
    items.sort(key=lambda i: (CAT_ORDER[i["cat"]], i["nom"]))
    return items


def build_sets(items, lang):
    """Sets Prime dont on possede au moins une piece (+ Warframes normales, voir plus bas).
    Chaque piece donne aussi « fabrique » : combien on en a deja construit (piece de Warframe
    sortie de la Fonderie, ou la Warframe / l'arme pour le schema principal)."""
    g, cat = game(), catalog()
    types = owned_types(STATE.inv)
    recipes = g.ex["recipes"]
    # file de la Fonderie : schema en cours -> (nombre, dont termines et a recuperer)
    pending, now_ms = {}, time.time() * 1000
    for e in (STATE.inv or {}).get("PendingRecipes", []) or []:
        if isinstance(e, dict) and e.get("ItemType"):
            d = e.get("CompletionDate") or {}
            fin = d.get("$date", {}).get("$numberLong") if isinstance(d.get("$date"), dict) else d.get("$date")
            n, prets = pending.get(e["ItemType"], (0, 0))
            pending[e["ItemType"]] = (n + 1, prets + (1 if fin and int(fin) <= now_ms else 0))

    def crafted(ref):
        res = (recipes.get(ref) or {}).get("resultType")
        if not res:
            return {}  # pas un schema (ex. canon d'arme) : rien a fabriquer
        n, prets = pending.get(ref, (0, 0))
        return {"fabrique": types.get(res, 0), "nom_fabrique": g.name(res, lang),
                "en_cours": n, "prets": prets}
    owned = {}
    for i in items:
        if i["cat"] == "prime" and i["slug"]:
            owned[i["slug"]] = owned.get(i["slug"], 0) + i["qte"]
    candidates = set()
    for slug in owned:
        k = slug.find("_prime")
        if k != -1 and slug[:k + 6] + "_set" in cat.by_slug:
            candidates.add(slug[:k + 6] + "_set")
    sets = []
    for s in sorted(candidates):
        try:
            parts = set_parts(s)
        except requests.RequestException:
            continue
        meta = cat.by_slug[s]
        root = meta.get("gameRef")
        base = g.name(root, lang) if root else None
        pieces = []
        for p in parts:
            pname = (g.name(p["ref"], lang) if p["ref"] else None) or cat.en(p["slug"])
            pieces.append({"slug": p["slug"], "nom": pname, "requis": p["qte"],
                           "possede": owned.get(p["slug"], 0), **crafted(p["ref"])})
        complets = min((p["possede"] // p["requis"] for p in pieces), default=0)
        sets.append({
            "id": "set:" + s, "cat": "set", "slug": s, "nom": base or cat.en(s),
            "nom_en": cat.en(s), "qte": complets, "rang": None, "sous_type": None,
            "ducats": meta.get("ducats"), "pieces": pieces,
            "vignette": cat.images(s)[0], "image": cat.images(s)[1], "rarete": "prime",
            "nb_possedees": sum(1 for p in pieces if p["possede"] >= p["requis"]),
            "nb_total": len(pieces),
        })
    sets += normal_warframe_sets(lang, types, crafted)
    sets.sort(key=lambda s: (s["nb_total"] - s["nb_possedees"], s["nom"]))
    return sets


def owned_types(inv):
    """Nombre possede de chaque objet du jeu (schemas, pieces fabriquees, Warframes, armes...)."""
    out = {}
    for k, v in (inv or {}).items():
        # XPInfo = experience gagnee (meme pour un item supprime), PendingRecipes = en Fonderie :
        # ce ne sont pas des objets possedes
        if isinstance(v, list) and k not in ("XPInfo", "PendingRecipes"):
            for e in v:
                if isinstance(e, dict) and e.get("ItemType"):
                    out[e["ItemType"]] = out.get(e["ItemType"], 0) + int(e.get("ItemCount") or 1)
    return out


COLLECTION_TYPES = {  # categorie du jeu -> onglet de la collection
    "Suits": "warframe", "LongGuns": "primaire", "Pistols": "secondaire", "Melee": "melee",
    "SpaceSuits": "archwing", "SpaceGuns": "archwing", "SpaceMelee": "archwing", "MechSuits": "archwing",
    "Sentinels": "compagnon", "KubrowPets": "compagnon", "SentinelWeapons": "compagnon",
}
FRAME_LIKE = {"Suits", "SpaceSuits", "MechSuits", "Sentinels", "KubrowPets"}  # 1000 XP x rang^2 (armes : 500)
_collection_cache = {}


def collection_view(lang):
    """Toutes les Warframes, armes, Archwings et compagnons du jeu, comme le Codex :
    construit (dans l'inventaire), maitrise (XP au rang max, garde meme si on l'a supprime),
    et pour ce qui manque, combien de pieces on a deja (schemas ou pieces)."""
    inv = STATE.inv or {}
    key = (lang, STATE.version)
    if key in _collection_cache:
        return _collection_cache[key]
    g = game()
    types = owned_types(inv)
    xp = {e.get("ItemType"): e.get("XP", 0) for e in inv.get("XPInfo", []) if isinstance(e, dict)}
    recipes = g.ex["recipes"]
    by_result = {r.get("resultType"): p for p, r in recipes.items() if r.get("resultType")}
    out = []
    for sect in ("warframes", "weapons", "sentinels"):
        for path, v in (g.ex.get(sect) or {}).items():
            cat = v.get("productCategory")
            onglet = COLLECTION_TYPES.get(cat)
            if not onglet or v.get("partType") or ("/Pets/" in path and cat == "Pistols"):
                continue  # pieces d'armes modulaires, griffes de compagnon...
            built, x = types.get(path, 0), xp.get(path, 0)
            if v.get("codexSecret") and not built and not x:
                continue  # secret du Codex tant qu'on ne l'a pas
            nom = g.name(path, lang)
            if not nom:
                continue
            cap = v.get("maxLevelCap") or 30
            par = 1000 if cat in FRAME_LIKE else 500
            rang = min(cap, int((x / par) ** 0.5)) if x else 0
            # pieces deja obtenues pour ce qui n'est pas construit (schema principal + pieces)
            schemas = None
            rec = by_result.get(path)
            if rec and not built:
                parts = [i["ItemType"] for i in recipes[rec].get("ingredients", [])
                         if i.get("ItemType") and not i["ItemType"].startswith("/Lotus/Types/Items/")]
                got = (1 if types.get(rec) else 0) + sum(
                    1 for p in parts if types.get(p) or types.get(by_result.get(p, "")))
                if got:
                    schemas = f"{got}/{len(parts) + 1}"
            out.append({"chemin": path, "nom": nom, "type": onglet, "icone": g.icon_url(path),
                        "construit": built, "maitrise": rang >= cap, "rang": rang, "rang_max": cap,
                        "schemas": schemas})
    out.sort(key=lambda c: c["nom"])
    _collection_cache.clear()
    _collection_cache[key] = out
    return out


def normal_warframe_sets(lang, types, crafted):
    """Sets des Warframes non Prime (absentes de Warframe Market) : schema principal +
    neuroptiques, chassis, systemes, d'apres les recettes du jeu. Seulement si on a une piece."""
    g = game()
    recipes = g.ex["recipes"]
    by_result = {r.get("resultType"): p for p, r in recipes.items() if r.get("resultType")}
    out = []
    for path, r in recipes.items():
        res = r.get("resultType") or ""
        if "/WarframeRecipes/" not in path or not res.startswith("/Lotus/Powersuits/") or "Prime" in res:
            continue
        comps = [i["ItemType"] for i in r.get("ingredients", []) if i.get("ItemType", "").endswith("Component")]
        if len(comps) != 3:
            continue
        refs = [path] + [by_result[c] for c in comps if c in by_result]
        pieces = []
        for ref in refs:
            pieces.append({"slug": None, "nom": g.name(ref, lang) or ref.rsplit("/", 1)[-1], "requis": 1,
                           "possede": types.get(ref, 0), **crafted(ref)})
        if not any(p["possede"] or p.get("fabrique") for p in pieces[1:]) and not pieces[0]["possede"]:
            continue  # aucune piece : on ne montre pas le set
        icon = g.icon_url(res)
        out.append({
            "id": "set:" + path, "cat": "set", "slug": None, "nom": g.name(res, lang) or res.rsplit("/", 1)[-1],
            "nom_en": g.name(res, "en"), "qte": min(p["possede"] for p in pieces), "rang": None,
            "sous_type": None, "ducats": None, "pieces": pieces, "vignette": icon, "image": icon,
            "rarete": "common", "normal": True,
            "nb_possedees": sum(1 for p in pieces if p["possede"] >= p["requis"]), "nb_total": len(pieces),
        })
    return out


# ================================================================ etat + lecture auto
class State:
    def __init__(self):
        self.inv = load_json(DUMP)
        self.version = 1 if self.inv else 0
        self.source = "copie" if self.inv else ""
        self.when = DUMP.stat().st_mtime if self.inv else 0
        self.auto = True
        self.interval = 60   # un scan prend ~15 s de calcul : pas plus d'une fois par minute
        self.scanning = False
        self.game_running = False
        self.error = ""
        self.scan_lock = threading.Lock()

    def scan(self):
        """Lit la memoire du jeu. Retourne True si l'inventaire a change."""
        with self.scan_lock:
            pid = pi.find_pid()
            self.game_running = bool(pid)
            if not pid:
                raise RuntimeError("jeu_absent")
            self.scanning = True
            try:
                inv, notes = pi.read_inventory(pid)
            finally:
                self.scanning = False
            if not inv:
                raise RuntimeError("inventaire_absent")
            changed = not self.inv or pi.sync_key(inv) != pi.sync_key(self.inv) \
                or self.source != "jeu"
            if changed:
                self.inv = inv
                self.version += 1
                self.source = "jeu"
                REWARDS.clear()  # le vrai inventaire contient maintenant les recompenses ajoutees en attendant
                self.when = time.time()
                save_json(DUMP, inv)
            self.error = ""
            return changed

    def loop(self):
        while True:
            if self.auto:
                try:
                    self.scan()
                except Exception as e:  # le jeu peut etre ferme : on reessaie plus tard
                    self.error = str(e)
            else:
                self.game_running = bool(pi.find_pid())
            time.sleep(self.interval)

    def follow_game_log(self, log=None):
        """Le jeu ne garde la copie de l'inventaire en memoire que peu de temps apres l'avoir
        recue (connexion, retour de mission, relais...). Son journal l'annonce
        (« OnInventoryResults » puis « Inventory sync done ») : on lit tout de suite a ce moment-la,
        au lieu d'attendre jusqu'a 60 s et de risquer de la manquer."""
        log = log or Path(os.environ.get("LOCALAPPDATA", "")) / "Warframe" / "EE.log"
        pos, buf = (log.stat().st_size if log.exists() else 0), b""
        while True:
            time.sleep(0.5)
            try:
                size = log.stat().st_size if log.exists() else 0
                if size < pos:  # le jeu a redemarre : nouveau journal
                    pos, buf = 0, b""
                if size <= pos:
                    continue
                with open(log, "rb") as f:
                    f.seek(pos)
                    buf += f.read(size - pos)
                pos = size
                *lines, buf = buf.split(b"\n")
                if self.auto and any(b"OnInventoryResults, body size" in ln for ln in lines):
                    threading.Thread(target=self.scan_after_sync, daemon=True).start()
            except OSError:
                pass

    def scan_after_sync(self):
        for delay in (0.5, 2, 4):  # la copie apparait juste apres ; deux essais de plus au besoin
            time.sleep(delay)
            try:
                self.scan()
                return
            except Exception as e:
                self.error = str(e)


STATE = State()


def history_add(entry):
    h = load_json(HISTORY, []) or []
    entry["t"] = int(time.time())
    if h and entry["t"] - h[-1]["t"] < 3600:
        h[-1] = entry   # un point par heure maximum
    else:
        h.append(entry)
    save_json(HISTORY, h[-2000:])
    return h


# ================================================================ mises a jour (GitHub Releases)
# Seuls ces fichiers peuvent etre remplaces : jamais les donnees de l'utilisateur
# (inventaire, session WFM, souhaits, ventes, caches...).
UPDATABLE = {"app.py", "index.html", "prime_inventory.py", "reliques_overlay.py", "chat_jeu.py", "Lancer.bat",
             "LISEZMOI.txt"}


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3])


def check_update():
    """Derniere version publiee. Desactive dans un dossier de developpement (.git)."""
    info = {"version": VERSION, "nouvelle": False}
    if (BASE / ".git").exists():
        return {**info, "dev": True}
    r = requests.get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", timeout=8,
                     headers={"Accept": "application/vnd.github+json"})
    if r.status_code == 404:  # depot pas encore publie
        return info
    r.raise_for_status()
    rel = r.json()
    latest = rel.get("tag_name", "").lstrip("v")
    wants = [EXE_ASSET, OLD_EXE_ASSET] if FROZEN else [SRC_ASSET]
    assets = {a["name"]: a for a in rel.get("assets", [])}
    asset = next((assets[w] for w in wants if w in assets), None)
    return {**info, "derniere": latest, "notes": rel.get("body") or "",
            "nouvelle": bool(asset) and version_tuple(latest) > version_tuple(VERSION),
            "url": asset["browser_download_url"] if asset else None}


def install_update():
    """Telecharge la nouvelle version, remplace le code, puis relance l'app."""
    info = check_update()
    if not info.get("nouvelle"):
        raise RuntimeError("pas_de_maj")
    url = info["url"]
    if not url.startswith(f"https://github.com/{UPDATE_REPO}/releases/download/"):
        raise RuntimeError("source de mise a jour inattendue")
    data = requests.get(url, timeout=120).content
    if FROZEN:
        exe = Path(sys.executable)
        old = exe.with_suffix(".old")
        old.unlink(missing_ok=True)
        os.replace(exe, old)  # Windows autorise a renommer un exe en cours d'execution
        exe.write_bytes(data)
        cmd = [str(exe)]
    else:
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for raw in z.namelist():
                name = raw.replace("\\", "/")  # zips faits sous Windows : "assets\x.png"
                if name.endswith("/"):
                    continue
                ok = name in UPDATABLE or (name.startswith("assets/") and name.count("/") == 1
                                           and ".." not in name)
                if not ok:
                    continue  # tout le reste est ignore (et rien hors du dossier de l'app)
                dest = BASE / name
                dest.parent.mkdir(exist_ok=True)
                tmp = dest.with_name(dest.name + ".maj")
                tmp.write_bytes(z.read(raw))
                os.replace(tmp, dest)
        cmd = [sys.executable, str(BASE / "app.py")]
    # la nouvelle version attend que celle-ci soit fermee avant de demarrer
    # exe "tout-en-un" : un processus lanceur (le parent) garde l'ancien exe ouvert jusqu'a la
    # fin ; la nouvelle version doit donc attendre ce parent, pas seulement ce processus-ci
    waited = os.getppid() if FROZEN else os.getpid()
    args = [a for i, a in enumerate(sys.argv[1:])  # memes options (port...), sans l'ancien --attendre-pid
            if a != "--attendre-pid" and (i == 0 or sys.argv[i] != "--attendre-pid")]
    subprocess.Popen(cmd + args + ["--attendre-pid", str(waited)], cwd=str(BASE), env=clean_env(),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    threading.Timer(3.0, shutdown).start()
    return {"ok": True, "version": info["derniere"]}


UPDATE_INFO = {}           # derniere verification des mises a jour (toutes les 30 min)
UPDATE_CHECK_EVERY = 30 * 60


def update_loop():
    """Verifie GitHub toutes les 30 min : l'interface affiche alors un bouton « Mettre a jour »."""
    while True:
        try:
            UPDATE_INFO.clear()
            UPDATE_INFO.update(check_update())
        except Exception as e:
            print("verification des mises a jour :", e)
        time.sleep(UPDATE_CHECK_EVERY)


OVERLAY = None   # overlay des recompenses de reliques
# Recompenses de reliques ajoutees tout de suite a l'inventaire affiche, en attendant la vraie
# synchro du jeu (qui n'arrive qu'en fin de mission) : [{id, t, vague, cartes, choix}]
REWARDS = []
_reward_id = [0]


_relic_names = {}


def relic_reward_names(lang):
    """{chemin: nom} de toutes les recompenses de reliques (pour corriger a la main)."""
    if lang not in _relic_names:
        g = game()
        out = {}
        for rel in g.ex["relics"].values():
            for r in (g.rewards().get(rel.get("rewardManifest")) or [[]])[0]:
                path = r["type"].replace("/StoreItems/", "/", 1)
                if path not in out:
                    nom = g.name(path, lang) or path.rsplit("/", 1)[-1]
                    out[path] = re.sub(r"<[^>]*>\s*", "", nom)  # ex. « <ARCHWING> Odonata Prime »
        _relic_names[lang] = out
    return _relic_names[lang]


def relic_reward_done(info):
    """Appele par l'overlay a la fermeture de l'ecran de recompenses. Par defaut on ajoute TA
    recompense (lue dans le journal du jeu, exacte) ; l'interface permet d'en choisir une autre."""
    cartes = info["cartes"]
    choix = next((i for i, c in enumerate(cartes) if c.get("a_moi")), 0)
    _reward_id[0] += 1
    REWARDS.append({"id": _reward_id[0], "t": time.time(), "vague": len(REWARDS) + 1,
                    "cartes": cartes, "choix": choix})
    STATE.version += 1  # l'interface recharge l'inventaire


def inventory_with_rewards(inv):
    """Copie de l'inventaire avec les recompenses en attente ajoutees ; et leur nombre par chemin."""
    if not REWARDS or not inv:
        return inv, {}
    recipes = game().ex["recipes"]
    inv = dict(inv)
    ajout = {}
    for r in REWARDS:
        c = r["cartes"][r["choix"]] if 0 <= r["choix"] < len(r["cartes"]) else None  # -1 = aucune
        path = c and c.get("chemin")
        if not path:
            continue
        ajout[path] = ajout.get(path, 0) + 1
    for path, n in ajout.items():
        key = "Recipes" if path in recipes else "MiscItems"
        lst = [dict(e) for e in inv.get(key, [])]
        e = next((e for e in lst if e.get("ItemType") == path), None)
        if e:
            e["ItemCount"] = int(e.get("ItemCount") or 1) + n
        else:
            lst.append({"ItemType": path, "ItemCount": n})
        inv[key] = lst
    return inv, ajout


def start_overlay():
    global OVERLAY
    if not pi.IS_WINDOWS:
        return
    try:
        import reliques_overlay
        OVERLAY = reliques_overlay.RewardOverlay(sys.modules[__name__])
        OVERLAY.on_reward = relic_reward_done   # fin de chaque vague : ajout provisoire a l'inventaire
        OVERLAY.start(RES / "assets", BASE)  # BASE : overlay.log + derniere capture lue
    except Exception as e:
        print("overlay des reliques :", e)


CHAT = None      # chat du jeu recopie et traduit


def start_chat():
    global CHAT
    if not pi.IS_WINDOWS:
        return
    try:
        import chat_jeu
        CHAT = chat_jeu.ChatReader(sys.modules[__name__])
        CHAT.start()
    except Exception as e:
        print("chat du jeu :", e)


def open_overlay_log(icon=None, item=None):
    """Ouvre overlay.log (ce que l'overlay a detecte, lu et reconnu a chaque ecran de recompenses)."""
    f = BASE / "overlay.log"
    if not f.exists():
        f.write_text("Aucun ecran de recompenses detecte pour l'instant.\n", encoding="utf-8")
    os.startfile(str(f))


def toggle_overlay(icon=None, item=None):
    if OVERLAY:
        OVERLAY.enabled = not OVERLAY.enabled


def shutdown():
    """Ferme proprement : sauvegarde les prix, repasse invisible sur WFM, quitte."""
    flush_prices()
    try:
        PRESENCE.auto = False
        PRESENCE.tick()
    except Exception:
        pass
    os._exit(0)


def wait_for_pid(pid, timeout=30):
    """Au redemarrage apres mise a jour : attend la fermeture de l'ancienne version."""
    end = time.time() + timeout
    while time.time() < end and pid and psutil_pid_exists(pid):
        time.sleep(0.3)
    remove_old_exe()


def remove_old_exe(tries=20):
    """Efface l'ancien exe garde apres une mise a jour. Ne plante jamais : s'il est encore
    verrouille, on reessaie un moment, puis on laisse tomber (nouvel essai au prochain lancement)."""
    if not FROZEN:
        return
    old = Path(sys.executable).with_suffix(".old")
    for _ in range(tries):
        try:
            old.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.5)


def psutil_pid_exists(pid):
    try:
        return pi.psutil.pid_exists(pid)
    except Exception:
        return False


# ================================================================ serveur HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self, write=False):
        """Bloque les autres sites ouverts dans le navigateur (DNS rebinding, formulaires pieges)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("localhost", "127.0.0.1", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
            return False
        # en-tete perso : un autre site ne peut pas l'envoyer sans pre-verification CORS (refusee)
        return not write or self.headers.get("X-Inventaire") == "1"

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self.trusted():
            return self.send(403, {"erreur": "interdit"})
        try:
            if u.path in ("/", "/index.html"):
                self.send(200, (RES / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path.startswith("/assets/"):  # icones (Lotus, platine, ducats)
                name = u.path.rsplit("/", 1)[1]
                f = RES / "assets" / name
                if "/" in name or "\\" in name or ".." in name or not f.is_file():
                    return self.send(404, {"erreur": "introuvable"})
                ctype = "image/x-icon" if name.endswith(".ico") else "image/png"
                self.send(200, f.read_bytes(), ctype)
            elif u.path == "/api/etat":
                self.send(200, {"version": STATE.version, "auto": STATE.auto,
                                "jeu": STATE.game_running, "scan": STATE.scanning,
                                "source": STATE.source, "quand": STATE.when,
                                "erreur": STATE.error, "langues": LANGS,
                                "presence": {"auto": PRESENCE.auto, "statut": PRESENCE.statut},
                                "nb_alertes": len(_alerts), "version_app": VERSION,
                                "maj": {k: UPDATE_INFO.get(k) for k in ("nouvelle", "derniere")},
                                "overlay": {"actif": bool(OVERLAY and OVERLAY.enabled),
                                            "statut": OVERLAY.status if OVERLAY else "indisponible"},
                                "recompense": REWARDS[-1] if REWARDS else None, "attente": len(REWARDS)})
            elif u.path == "/api/chat":  # messages du chat du jeu (avec traduction)
                if not CHAT:
                    return self.send(200, {"actif": False, "statut": "indisponible", "messages": []})
                msgs = CHAT.depuis(int(q.get("depuis") or 0), float(q.get("maj") or 0))
                self.send(200, {"actif": CHAT.enabled, "statut": CHAT.statut, "messages": msgs,
                                "maintenant": time.time()})
            elif u.path == "/api/maj":  # aussi le petit bouton "rechercher les mises a jour"
                info = check_update()
                UPDATE_INFO.clear()
                UPDATE_INFO.update(info)
                self.send(200, info)
            elif u.path == "/api/baro":
                self.send(200, baro())
            elif u.path == "/api/alertes":
                self.send(200, list(_alerts.values()))
            elif u.path == "/api/souhaits":
                self.send(200, wishlist_view(q.get("lang", "fr")))
            elif u.path == "/api/catalogue":
                self.send(200, catalog_view(q.get("lang", "fr")))
            elif u.path == "/api/relique":
                self.send(200, relic_value(q["chemin"], q.get("lang", "fr")))
            elif u.path == "/api/historique-prix":
                rank = int(q["rang"]) if q.get("rang", "") not in ("", "null") else None
                self.send(200, price_history(q["slug"], rank))
            elif u.path == "/api/mes-ventes":  # ventes marquees « Vendu » (onglet Market > Stats)
                self.send(200, load_json(SALES_LOG, []) or [])
            elif u.path == "/api/collection":  # Warframes et armes du jeu : construit / maitrise
                self.send(200, collection_view(q.get("lang", "fr") if q.get("lang") in LANGS else "fr"))
            elif u.path == "/api/volume":
                rank = int(q["rang"]) if q.get("rang", "") not in ("", "null") else None
                self.send(200, volume(q["slug"], rank))
            elif u.path == "/api/inventaire":
                lang = q.get("lang", "fr") if q.get("lang") in LANGS else "fr"
                if lang != CURRENT_LANG[0]:
                    CURRENT_LANG[0] = lang
                    try:
                        account().load_orders() if account().token else None
                    except Exception:
                        pass
                if q.get("lire") == "1":
                    STATE.scan()
                if not STATE.inv:
                    raise RuntimeError("aucune_copie")
                inv, ajout = inventory_with_rewards(STATE.inv)
                items = build_items(inv, lang)
                for it in items:  # etiquette « en attente de synchro »
                    if it.get("chemin") in ajout:
                        it["attente"] = ajout[it["chemin"]]
                self.send(200, {"items": items, "sets": build_sets(items, lang),
                                "version": STATE.version, "source": STATE.source,
                                "quand": STATE.when})
            elif u.path == "/api/prix":
                rank = int(q["rang"]) if q.get("rang", "") not in ("", "null") else None
                self.send(200, prices(q["slug"], rank, q.get("sous_type") or None,
                                      q.get("frais") == "1", q.get("cache") == "1"))
            elif u.path == "/api/historique":
                self.send(200, load_json(HISTORY, []) or [])
            elif u.path == "/api/compte":
                acc = account()
                self.send(200, acc.refresh() if q.get("maj") == "1" else acc.state())
            else:
                self.send(404, {"erreur": "introuvable"})
        except Exception as e:  # l'interface affiche l'erreur
            self.send(500, {"erreur": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if not self.trusted(write=True):
            return self.send(403, {"erreur": "interdit"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            acc = account()
            if u.path == "/api/connexion":
                self.send(200, acc.login(str(body.get("email", "")), str(body.get("password", ""))))
                _watch_now.set()
            elif u.path == "/api/vendre-lot":
                self.send(200, acc.sell_many(body.get("lots") or []))
            elif u.path == "/api/souhaits":
                rank = body.get("rang")
                self.send(200, wishlist_set(body["slug"], None if rank in ("", None) else int(rank),
                                            body.get("prix_max", 0), bool(body.get("retirer"))))
            elif u.path == "/api/maj/installer":
                self.send(200, install_update())
            elif u.path == "/api/afficher":  # 2e lancement de l'app : on montre la fenetre deja ouverte
                if not WINDOW:
                    raise RuntimeError("pas_de_fenetre")
                show_window()
                self.send(200, {"ok": True})
            elif u.path == "/api/chat":
                if CHAT:
                    CHAT.enabled = bool(body.get("actif"))
                self.send(200, {"actif": bool(CHAT and CHAT.enabled)})
            elif u.path == "/api/recompense/choisir":  # « ce n'est pas celle-la » : autre carte prise
                r = next((x for x in REWARDS if x["id"] == int(body.get("id") or 0)), None)
                if not r:
                    raise ValueError("recompense inconnue")
                if body.get("chemin"):  # aucune carte n'etait la bonne : item choisi a la main
                    path = str(body["chemin"])
                    if path not in relic_reward_names(CURRENT_LANG[0]):
                        raise ValueError("item inconnu")
                    r["cartes"] = [c for c in r["cartes"] if not c.get("manuel")] + [
                        {"chemin": path, "nom": game().name(path, CURRENT_LANG[0]), "prix": None,
                         "a_moi": False, "manuel": True}]
                    r["choix"] = len(r["cartes"]) - 1
                elif int(body.get("choix", -2)) == -1:  # aucune : on n'ajoute rien
                    r["choix"] = -1
                elif 0 <= int(body.get("choix", -2)) < len(r["cartes"]):
                    r["choix"] = int(body["choix"])
                else:
                    raise ValueError("choix inconnu")
                STATE.version += 1
                self.send(200, r)
            elif u.path == "/api/recompenses-possibles":  # pour choisir l'item a la main
                names = relic_reward_names(body.get("lang") if body.get("lang") in LANGS else "fr")
                self.send(200, sorted(({"chemin": k, "nom": v} for k, v in names.items()), key=lambda x: x["nom"]))
            elif u.path == "/api/overlay":
                if OVERLAY:
                    OVERLAY.enabled = bool(body.get("actif"))
                self.send(200, {"actif": bool(OVERLAY and OVERLAY.enabled)})
            elif u.path == "/api/presence":
                PRESENCE.auto = bool(body.get("auto"))
                self.send(200, {"auto": PRESENCE.auto, "statut": PRESENCE.statut})
            elif u.path == "/api/deconnexion":
                acc.logout()
                self.send(200, acc.state())
            elif u.path == "/api/vendre":
                self.send(200, acc.sell(body["slug"], body["prix"], body["qte"], body.get("rang"),
                                        body.get("sous_type"), body.get("visible", True)))
            elif u.path == "/api/offre/modifier":
                self.send(200, acc.update(body["id"], body["prix"], body["qte"],
                                          body.get("visible", True)))
                _alerts.pop(next((k for k, a in _alerts.items() if a.get("offre") == body["id"]), None), None)
            elif u.path == "/api/offre/vendu":
                self.send(200, acc.close(body["id"], body.get("qte", 1)))
            elif u.path == "/api/offre/supprimer":
                self.send(200, acc.delete(body["id"]))
            elif u.path == "/api/historique":  # envoye quand tous les prix sont charges
                flush_prices()
                entry = {k: int(body.get(k) or 0) for k in ("median", "min", "ducats")}
                self.send(200, history_add(entry))
            elif u.path == "/api/auto":
                STATE.auto = bool(body.get("auto"))
                self.send(200, {"auto": STATE.auto})
            else:
                self.send(404, {"erreur": "introuvable"})
        except Exception as e:
            self.send(500, {"erreur": str(e)})


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # sous Windows, True laisserait 2 copies de l'app sur le meme port


class Server6(Server):
    address_family = socket.AF_INET6


def replace_old_instance(port):
    """L'app qui occupe deja le port est plus ancienne que celle-ci ? On l'arrete et on renvoie
    notre serveur. Sinon (meme version ou plus recente, ou autre programme) : None."""
    try:
        r = requests.get(f"http://127.0.0.1:{port}/api/etat", timeout=4, headers={"X-Inventaire": "1"})
        other = r.json().get("version_app")  # absent dans les toutes premieres versions
    except Exception:
        other = None
    if other and version_tuple(other) >= version_tuple(VERSION):
        return None
    try:
        import psutil
        pids = {c.pid for c in psutil.net_connections("tcp")
                if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid}
        procs = []
        for pid in pids - {os.getpid()}:
            p = psutil.Process(pid)
            nom = " ".join([p.name()] + (p.cmdline() or [])).lower()
            if any(k in nom for k in ("lotusdesk", "inventairewarframe", "app.py")):  # seulement notre app
                procs.append(p)
        if not procs:
            return None
        print(f"Ancienne version ({other or 'inconnue'}) encore ouverte : je la ferme pour demarrer la {VERSION}.")
        for p in procs:
            for child in p.children(recursive=True):
                child.kill()
            p.kill()
        psutil.wait_procs(procs, timeout=5)
    except Exception as e:
        print("impossible de fermer l'ancienne version :", e)
        return None
    for _ in range(20):  # le port se libere juste apres
        try:
            return Server(("127.0.0.1", port), Handler)
        except OSError:
            time.sleep(0.25)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--sans-navigateur", action="store_true")
    ap.add_argument("--sans-icone", action="store_true", help="pas d'icone dans la barre des taches")
    ap.add_argument("--navigateur", action="store_true", help="page dans le navigateur au lieu d'une fenetre")
    ap.add_argument("--attendre-pid", type=int, default=0, help=argparse.SUPPRESS)  # apres mise a jour
    args = ap.parse_args()
    if args.attendre_pid:
        wait_for_pid(args.attendre_pid)
    else:  # reste d'une mise a jour precedente (ex. 1.0.3 qui a plante sur ce point)
        threading.Thread(target=remove_old_exe, args=(4,), daemon=True).start()
    if sys.stdout is None:  # lance avec pyw.exe (sans console) : les print iraient nulle part
        sys.stdout = sys.stderr = open(BASE / "app.log", "a", encoding="utf-8")
    url = f"http://localhost:{args.port}"
    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        # une ancienne version encore rangee pres de l'horloge : on la ferme et on prend sa place
        # (sinon on rouvrirait sa fenetre et c'est elle qui continuerait de tourner, overlay compris)
        srv = replace_old_instance(args.port)
        if not srv:
            print(f"L'app est deja ouverte (port {args.port} occupe) : je la remets au premier plan.")
            if not args.sans_navigateur:
                try:
                    requests.post(f"http://127.0.0.1:{args.port}/api/afficher", timeout=5,
                                  headers={"X-Inventaire": "1"}).raise_for_status()
                except requests.RequestException:
                    webbrowser.open(url)  # ancienne version ou mode navigateur
            return
    print("Chargement des donnees du jeu et de Warframe Market...")
    game().tr("fr")
    catalog()
    threading.Thread(target=STATE.loop, daemon=True).start()
    threading.Thread(target=STATE.follow_game_log, daemon=True).start()  # lecture des la synchro du jeu
    # "localhost" essaie d'abord IPv6 : sans ecoute sur ::1, chaque requete perd ~250 ms
    try:
        srv6 = Server6(("::1", args.port), Handler)
        threading.Thread(target=srv6.serve_forever, daemon=True).start()
    except OSError:
        pass
    threading.Thread(target=shared_loop, daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()
    threading.Thread(target=presence_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    start_overlay()
    start_chat()
    print(f"App prete : {url}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with_tray = not args.sans_icone
    if args.sans_navigateur:  # serveur seul (tests)
        wait_forever()
    elif args.navigateur or not run_window(url, with_tray):
        webbrowser.open(url)  # ancien mode : page dans le navigateur
        if not (with_tray and run_tray_blocking(lambda: webbrowser.open(url))):
            print("Ferme cette fenetre pour arreter l'app.")
            wait_forever()
    flush_prices()
    try:
        PRESENCE.auto = False
        PRESENCE.tick()  # repasse "invisible" sur WFM avant de quitter
    except Exception:
        pass


ICON_PNG = RES / "assets" / "lotus_app.png"
ICON_ICO = RES / "assets" / "lotus.ico"


def set_app_identity():
    """Windows regroupe la barre des taches par identite d'app : sans ca, on aurait l'icone de Python."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Thigrou.LotusDesk")
    except Exception:
        pass


def set_window_icon(title=APP_NAME):
    """Pose le Lotus sur la fenetre (barre de titre + barre des taches)."""
    try:
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = ctypes.c_void_p
        u32.LoadImageW.restype = ctypes.c_void_p
        u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = u32.FindWindowW(None, title)
        if not hwnd or not ICON_ICO.exists():
            return
        for which, size in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
            h = u32.LoadImageW(None, str(ICON_ICO), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u32.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
    except Exception:
        pass


def tray_image():
    from PIL import Image, ImageDraw
    if ICON_PNG.exists():  # le vrai symbole du Lotus
        return Image.open(ICON_PNG).convert("RGBA")
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(23, 26, 33, 255), outline=(217, 180, 90, 255), width=4)
    d.polygon([(16, 20), (24, 46), (32, 30), (40, 46), (48, 20), (42, 20), (40, 34), (32, 20),
               (24, 34), (22, 20)], fill=(217, 180, 90, 255))
    return img


def wait_forever():
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def make_tray(open_fn, quit_fn):
    """Icone dans la barre des taches (menu + notifications), ou None si pystray manque."""
    global _notify
    try:
        import pystray
    except ImportError:
        return None

    def toggle_auto(icon, item):
        STATE.auto = not STATE.auto

    def toggle_presence(icon, item):
        PRESENCE.auto = not PRESENCE.auto

    icon = pystray.Icon("lotus-desk", tray_image(), APP_NAME, menu=pystray.Menu(
        pystray.MenuItem("Ouvrir l'inventaire", lambda i, it: open_fn(), default=True),
        pystray.MenuItem("Lecture auto du jeu", toggle_auto, checked=lambda it: STATE.auto),
        pystray.MenuItem("Statut « en jeu » sur Warframe Market", toggle_presence,
                         checked=lambda it: PRESENCE.auto),
        pystray.MenuItem("Prix des récompenses de reliques (overlay)", toggle_overlay,
                         checked=lambda it: bool(OVERLAY and OVERLAY.enabled)),
        pystray.MenuItem("Ouvrir le journal de l'overlay", open_overlay_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter", lambda i, it: quit_fn())))
    _notify = lambda msg, title: icon.notify(msg, title)
    return icon


def run_tray_blocking(open_fn):
    """Mode navigateur : l'icone garde l'app en vie jusqu'a « Quitter »."""
    holder = {}
    icon = make_tray(open_fn, lambda: holder["icon"].stop())
    if not icon:
        return False
    holder["icon"] = icon
    hide_console()
    icon.run()
    return True


WINDOW = None  # fenetre de l'app (pywebview), pour la reafficher depuis l'icone ou un 2e lancement


def show_window():
    if WINDOW:
        WINDOW.show()
        WINDOW.restore()


def run_window(url, with_tray):
    """Ouvre l'interface dans une vraie fenetre Windows (moteur Edge WebView2).
    Fermer la fenetre la range dans la barre des taches ; « Quitter » arrete l'app."""
    global WINDOW
    try:
        import webview
    except ImportError:
        return False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # liens WFM : navigateur habituel
    quitting = {"oui": False}
    WINDOW = webview.create_window(APP_NAME, url, width=1300, height=850,
                                   min_size=(420, 500), background_color="#0f1115")

    def quit_app():
        quitting["oui"] = True
        WINDOW.destroy()

    icon = make_tray(show_window, quit_app) if with_tray else None

    def on_closing():
        if icon and not quitting["oui"]:
            WINDOW.hide()  # l'app continue (alertes, statut en jeu) dans la barre des taches
            return False
        return True

    WINDOW.events.closing += on_closing
    WINDOW.events.shown += lambda: set_window_icon()
    if icon:
        icon.run_detached()
    hide_console()
    set_app_identity()
    # dossier de profil fixe : garde la langue, le seuil, etc. d'un lancement a l'autre
    webview.start(private_mode=False, storage_path=str(BASE / "fenetre"),
                  icon=str(ICON_ICO) if ICON_ICO.exists() else None)
    if icon:
        icon.stop()
    return True


def hide_console():
    """Cache la fenetre noire si elle n'appartient qu'a l'app (double-clic sur l'exe)."""
    if not pi.IS_WINDOWS:
        return
    k32 = ctypes.WinDLL("kernel32")
    procs = (ctypes.c_ulong * 8)()
    if k32.GetConsoleProcessList(procs, 8) == 1:
        ctypes.WinDLL("user32").ShowWindow(k32.GetConsoleWindow(), 0)


if __name__ == "__main__":
    main()
