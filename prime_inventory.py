"""
Liste tes items Prime (parts + blueprints) en FRANCAIS avec leur valeur Warframe Market.

- Windows uniquement, Python 3.9+ 64 bits
- py -m pip install psutil requests
- Acces MEMOIRE EN LECTURE SEULE au processus du jeu (comme le fait Overwolf/AlecaFrame).
  Outil non officiel : utilisation a tes risques.
- Les noms francais viennent des fichiers de traduction du jeu (depot
  warframe-public-export-plus), telecharges une fois dans le dossier "wf_data".

Utilisation :
    py prime_inventory.py                       -> s'arrete des que l'inventaire est lu
    py prime_inventory.py --attendre-changement -> attend que le jeu resynchronise (relais <-> orbiteur)
    py prime_inventory.py --intervalle 10       -> secondes entre deux scans (defaut 5)
    py prime_inventory.py --maj-donnees         -> re-telecharge les fichiers de traduction
    py prime_inventory.py --dump inventory_dump.json
                                                -> n'utilise pas la memoire, relit une copie deja faite
Ctrl+C pour arreter.
"""
import argparse
import csv
import json
import os
import re
import struct
import sys
import time
from collections import namedtuple
from pathlib import Path

import requests

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    import psutil

PROCESS_NAME = "warframe.x64.exe"
MARKER = b'"LastInventorySync"'
WFM = "https://api.warframe.market/v2"
HEADERS = {"Language": "en", "Platform": "pc", "Accept": "application/json"}

DATA_URL = "https://raw.githubusercontent.com/calamity-inc/warframe-public-export-plus/senpai/"
DATA_FILES = ["dict.fr.json", "dict.en.json", "ExportResources.json", "ExportRecipes.json",
              "ExportWarframes.json", "ExportWeapons.json", "ExportSentinels.json"]
DATA_DIR = Path(__file__).resolve().parent / "wf_data"
DATA_MAX_AGE = 7 * 24 * 3600  # re-telecharge au bout de 7 jours

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
MAX_REGION = 1 << 30           # on ignore les regions > 1 Go
MAX_BACK = 256 * 1024 * 1024   # distance max marqueur -> debut du JSON
MAX_FWD = 256 * 1024 * 1024    # distance max marqueur -> fin du JSON

BRACKETS = re.compile(rb"[\[\]{}]")
Region = namedtuple("Region", "base size state protect type")

if IS_WINDOWS:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wt.DWORD),
            ("PartitionId", wt.WORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wt.DWORD),
            ("Protect", wt.DWORD),
            ("Type", wt.DWORD),
        ]

    k32.OpenProcess.restype = wt.HANDLE
    k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t)]


def now():
    return time.strftime("%H:%M:%S")


def find_pid():
    for p in psutil.process_iter(["name", "pid"]):
        if (p.info["name"] or "").lower() == PROCESS_NAME:
            return p.info["pid"]
    return None


def sync_key(obj):
    """LastInventorySync est souvent {"$oid": "..."} : on le ramene a une chaine comparable."""
    v = obj.get("LastInventorySync")
    if isinstance(v, dict):
        v = v.get("$oid", json.dumps(v, sort_keys=True))
    return "" if v is None else str(v)


def clean(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


# ================================================================ lecture memoire
def enum_regions(h):
    regions = []
    addr = 0
    mbi = MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        regions.append(Region(base, size, mbi.State, mbi.Protect, mbi.Type))
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt
    return regions


def region_ok(r):
    return (r.state == MEM_COMMIT and not (r.protect & (PAGE_NOACCESS | PAGE_GUARD))
            and 0 < r.size <= MAX_REGION)


def make_reader(h):
    def read(base, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size, ctypes.byref(got))
        return buf.raw[:got.value]
    return read


def scan_back(data, end, depth):
    """
    Remonte depuis `end` en comptant accolades/crochets. Retourne (offset du '{' racine, depth),
    (-1, depth) si on est dans un tableau, ou (None, depth) si pas trouve dans ce bloc.
    """
    chunk_size = 4 * 1024 * 1024
    pos = end
    while pos > 0:
        start = max(0, pos - chunk_size)
        chunk = data[start:pos]
        for m in reversed(list(BRACKETS.finditer(chunk))):
            c = chunk[m.start()]
            if c in (0x7D, 0x5D):  # } ]
                depth += 1
            elif depth == 0:
                return (start + m.start() if c == 0x7B else -1), depth
            else:
                depth -= 1
        pos = start
    return None, depth


def scan_fwd(data, start, depth):
    """Avance en comptant. Retourne (offset apres l'accolade fermante racine, depth) ou (None, depth)."""
    for m in BRACKETS.finditer(data, start):
        c = data[m.start()]
        if c in (0x7B, 0x5B):  # { [
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.end(), depth
    return None, depth


def neighbor(regions, idx, step):
    j = idx + step
    if 0 <= j < len(regions):
        a, b = (regions[j], regions[idx]) if step < 0 else (regions[idx], regions[j])
        if a.base + a.size == b.base and region_ok(regions[j]):
            return j
    return None


def describe_gap(regions, idx, data):
    c = regions[idx]
    txt = f"zone {c.size} octets @0x{c.base:x} ; debut de zone : {clean(data[:60])!r} ; "
    if idx == 0:
        return txt + "aucune zone avant"
    p = regions[idx - 1]
    adj = "contigue" if p.base + p.size == c.base else "NON contigue"
    return (txt + f"zone precedente {adj}, etat=0x{p.state:x} protection=0x{p.protect:x} "
            f"taille={p.size}")


def extract_across(read, regions, idx, data, i):
    """Extrait le JSON autour du marqueur (en data[i:]), en traversant les zones contigues."""
    chain = [data]
    cur_idx, cur_data, cur_end, depth, back = idx, data, i, 0, 0
    while True:
        off, depth = scan_back(cur_data, cur_end, depth)
        if off is not None:
            break
        p = neighbor(regions, cur_idx, -1)
        if p is None:
            return None, "debut du JSON introuvable : " + describe_gap(regions, cur_idx, cur_data)
        back += len(cur_data)
        if back > MAX_BACK:
            return None, "debut du JSON trop loin du marqueur"
        cur_idx = p
        cur_data = read(regions[p].base, regions[p].size)
        chain.insert(0, cur_data)
        cur_end = len(cur_data)
    if off == -1:
        return None, "le marqueur est dans un tableau, pas dans l'objet racine"

    end_off, depth = scan_fwd(data, i, 1)
    n, fwd, fwd_total = idx, [], 0
    while end_off is None:
        nx = neighbor(regions, n, +1)
        if nx is None:
            return None, "fin du JSON introuvable (zone suivante absente ou illisible)"
        fwd_total += regions[nx].size
        if fwd_total > MAX_FWD:
            return None, "fin du JSON trop loin du marqueur"
        n = nx
        d = read(regions[n].base, regions[n].size)
        fwd.append(d)
        end_off, depth = scan_fwd(d, 0, depth)
    if not fwd:
        if len(chain) == 1:
            blob = data[off:end_off]
        else:
            blob = chain[0][off:] + b"".join(chain[1:-1]) + data[:end_off]
    else:
        fwd[-1] = fwd[-1][:end_off]
        blob = chain[0][off:] + b"".join(chain[1:]) + b"".join(fwd)

    try:
        obj = json.loads(blob.decode("utf-8", errors="ignore"))
    except ValueError as e:
        return None, f"JSON invalide ({str(e)[:80]}), {len(blob)} octets assembles"
    if isinstance(obj, dict) and "LastInventorySync" in obj:
        return obj, ""
    return None, "objet lu sans LastInventorySync"


def read_inventory(pid):
    """Retourne (inventaire ou None, liste de notes de diagnostic)."""
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise OSError("OpenProcess a echoue (lance le terminal avec le meme niveau de droits "
                      "que le jeu).")
    best, notes = None, []
    try:
        read = make_reader(h)
        regions = enum_regions(h)
        for idx, r in enumerate(regions):
            if not region_ok(r):
                continue
            data = read(r.base, r.size)
            i = data.find(MARKER)
            while i != -1:
                obj, note = extract_across(read, regions, idx, data, i)
                if obj:
                    if best is None or sync_key(obj) >= sync_key(best):
                        best = obj
                else:
                    notes.append(note)
                i = data.find(MARKER, i + 1)
    finally:
        k32.CloseHandle(h)
    return best, notes


def wait_for_inventory(interval, wait_change):
    """Re-scanne la memoire jusqu'a obtenir un inventaire (ou un nouvel inventaire)."""
    baseline = None
    last_msg = None

    def say(msg):
        nonlocal last_msg
        if msg != last_msg:
            print(f"[{now()}] {msg}")
            last_msg = msg

    print("Le script reste allume. Ctrl+C pour arreter.")
    print("Astuce : le jeu resynchronise l'inventaire pendant les chargements "
          "(va au relais puis retourne dans ton orbiteur).\n")
    while True:
        pid = find_pid()
        if not pid:
            say("En attente du lancement de Warframe...")
            time.sleep(interval)
            continue
        try:
            inv, notes = read_inventory(pid)
        except OSError as e:
            say(f"Erreur d'acces au jeu : {e}")
            time.sleep(interval)
            continue

        if not inv:
            if notes:
                say(f"Marqueur trouve mais inventaire illisible : {notes[0]}")
            else:
                say("Jeu detecte, inventaire pas encore en memoire. Connecte-toi / change de "
                    "zone (relais <-> orbiteur)...")
            time.sleep(interval)
            continue

        key = sync_key(inv)
        if not wait_change:
            print(f"[{now()}] Inventaire trouve (sync {key}).")
            return inv
        if baseline is None:
            baseline = key
            say(f"Inventaire de depart lu (sync {key}). Va au relais puis retourne dans "
                "l'orbiteur : j'attends que le jeu resynchronise...")
        elif key != baseline:
            print(f"[{now()}] Changement detecte (sync {baseline} -> {key}). Inventaire a jour.")
            return inv
        time.sleep(interval)


# ================================================================ noms (traductions du jeu)
def load_game_data(data_dir=DATA_DIR, force=False):
    """Telecharge (si besoin) puis charge les fichiers de traduction et d'export du jeu."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for fn in DATA_FILES:
        path = data_dir / fn
        fresh = path.exists() and (time.time() - path.stat().st_mtime) < DATA_MAX_AGE
        if fresh and not force:
            continue
        try:
            print(f"Telechargement de {fn}...")
            r = requests.get(DATA_URL + fn, timeout=120)
            r.raise_for_status()
            path.write_bytes(r.content)
        except requests.RequestException as e:
            if path.exists():
                print(f"  echec ({e}) : j'utilise la copie locale existante.")
            else:
                sys.exit(f"Impossible de telecharger {fn} : {e}")

    def load(fn):
        with open(Path(data_dir) / fn, encoding="utf-8") as f:
            return json.load(f)

    return {
        "fr": load("dict.fr.json"),
        "en": load("dict.en.json"),
        "sections": [load("ExportResources.json"), load("ExportWarframes.json"),
                     load("ExportWeapons.json"), load("ExportSentinels.json")],
        "recipes": load("ExportRecipes.json"),
    }


def item_name(path, tr, data, depth=0):
    """Nom de l'item dans la langue du dictionnaire `tr`, ou None si introuvable."""
    if depth > 3:
        return None
    for sect in data["sections"]:
        e = sect.get(path)
        if e and e.get("name"):
            return tr.get(e["name"])
    rec = data["recipes"].get(path)
    if rec and rec.get("resultType"):
        inner = item_name(rec["resultType"], tr, data, depth + 1)
        tmpl = tr.get("/Lotus/Language/Items/BlueprintAndItem")  # "|ITEM| (Schema)"
        if inner and tmpl:
            return tmpl.replace("|ITEM|", inner)
    return None


def is_prime_part(path):
    """Parts et blueprints Prime (exclut ducats, decorations, etc.)."""
    return path.startswith("/Lotus/Types/Recipes/") and "Prime" in path


def prime_items(inv):
    """Retourne {chemin interne: quantite} pour les parts/blueprints Prime."""
    found = {}
    for section in ("MiscItems", "Recipes"):
        for it in inv.get(section, []) or []:
            path = it.get("ItemType", "")
            if is_prime_part(path):
                found[path] = found.get(path, 0) + int(it.get("ItemCount", 1))
    return found


# ================================================================ Warframe Market
def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def wfm_catalog():
    """Index WFM : par nom anglais normalise, par chemin interne et par slug."""
    r = requests.get(f"{WFM}/items", headers=HEADERS, timeout=30)
    r.raise_for_status()
    by_name, by_ref, slugs = {}, {}, set()
    for it in r.json()["data"]:
        slug = it["slug"]
        slugs.add(slug)
        en = (it.get("i18n", {}).get("en", {}) or {}).get("name")
        if en:
            by_name[norm(en)] = slug
        if it.get("gameRef"):
            by_ref[it["gameRef"]] = slug
    return by_name, by_ref, slugs


def find_slug(path, en_name, catalog):
    by_name, by_ref, slugs = catalog
    if path in by_ref:
        return by_ref[path]
    if en_name:
        if norm(en_name) in by_name:
            return by_name[norm(en_name)]
        guess = re.sub(r"[^a-z0-9]+", "_", en_name.lower().replace("&", "and")).strip("_")
        if guess in slugs:
            return guess
    return None


def lowest_sell(slug):
    r = requests.get(f"{WFM}/orders/item/{slug}/top", headers=HEADERS, timeout=30)
    if r.status_code == 429:
        time.sleep(2)
        r = requests.get(f"{WFM}/orders/item/{slug}/top", headers=HEADERS, timeout=30)
    r.raise_for_status()
    sells = r.json()["data"].get("sell", [])
    prices = sorted(o["platinum"] for o in sells)
    return prices[0] if prices else 0


def build_rows(prime, data, catalog, fetch_price=lowest_sell, pause=0.4):
    """Retourne (lignes [(nom_fr, qte, prix|None, total)], nb items sans nom francais)."""
    rows, no_name = [], 0
    for path, qty in prime.items():
        fr = item_name(path, data["fr"], data)
        en = item_name(path, data["en"], data)
        if not fr:
            no_name += 1
            fr = en  # a defaut, le nom anglais ; jamais le chemin /Lotus/...
        if not fr:
            continue
        slug = find_slug(path, en, catalog)
        if slug:
            price = fetch_price(slug)
            time.sleep(pause)  # limite WFM : 3 requetes/s
        else:
            price = None
        rows.append((fr, qty, price, qty * price if price is not None else 0))
    rows.sort(key=lambda r: (r[3], r[1]), reverse=True)
    return rows, no_name


def print_report(rows, no_name):
    width = max([len(r[0]) for r in rows] + [10])
    print(f"\n{'Item':{width}} {'Qte':>4} {'Prix':>7} {'Total':>8}")
    for name, qty, price, tot in rows:
        p = f"{price}p" if price is not None else "  -"
        t = f"{tot}p" if price is not None else "  -"
        print(f"{name:{width}} {qty:>4} {p:>7} {t:>8}")
    priced = [r for r in rows if r[2] is not None]
    total = sum(r[3] for r in priced)
    print(f"\nTOTAL : {total} platines ({len(priced)} items avec prix sur {len(rows)})")
    if len(priced) < len(rows):
        print("(\"-\" = pas d'offre/pas d'item correspondant sur Warframe Market, non compte)")
    if no_name:
        print(f"{no_name} item(s) sans traduction francaise : nom anglais affiche.")


def main():
    ap = argparse.ArgumentParser(description="Valeur de tes items Prime (Warframe Market).")
    ap.add_argument("--attendre-changement", action="store_true",
                    help="attend que le jeu resynchronise l'inventaire (relais <-> orbiteur)")
    ap.add_argument("--intervalle", type=float, default=5.0,
                    help="secondes entre deux scans memoire (defaut : 5)")
    ap.add_argument("--maj-donnees", action="store_true",
                    help="re-telecharge les fichiers de traduction")
    ap.add_argument("--dump", help="lit un inventaire deja enregistre (JSON) au lieu de la memoire")
    args = ap.parse_args()

    if args.dump:
        with open(args.dump, encoding="utf-8") as f:
            inv = json.load(f)
    else:
        if not IS_WINDOWS:
            sys.exit("La lecture memoire ne marche que sous Windows (sinon utilise --dump).")
        if struct.calcsize("P") * 8 != 64:
            sys.exit("Python 32 bits detecte : installe Python 64 bits.")
        try:
            inv = wait_for_inventory(max(args.intervalle, 1.0), args.attendre_changement)
        except KeyboardInterrupt:
            sys.exit("\nArrete par l'utilisateur.")
        with open("inventory_dump.json", "w", encoding="utf-8") as f:
            json.dump(inv, f)
        print("Copie brute enregistree : inventory_dump.json")

    data = load_game_data(force=args.maj_donnees)
    prime = prime_items(inv)
    print(f"{len(prime)} types d'items Prime trouves. Recuperation des prix WFM...")
    catalog = wfm_catalog()

    rows, no_name = build_rows(prime, data, catalog)
    print_report(rows, no_name)

    with open("prime_inventory.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["item", "quantite", "prix_plat", "total_plat"])
        for name, qty, price, tot in rows:
            w.writerow([name, qty, "" if price is None else price, "" if price is None else tot])
    print("\nFichier genere : prime_inventory.csv (ouvrable dans Excel)")


if __name__ == "__main__":
    main()
