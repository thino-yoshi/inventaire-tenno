"""
Releve les prix de vente de tous les items de Warframe Market et ecrit prix.json.gz.

Lance par une GitHub Action toutes les 6 heures : l'app telecharge ce fichier au demarrage et
affiche tous les prix tout de suite, au lieu d'interroger WFM item par item (~3 req/s maximum).

    python tools/snapshot_prix.py prix.json.gz          -> tous les items (~25 min)
    python tools/snapshot_prix.py prix.json.gz 40       -> 40 items seulement (test)

Format : {"t": horodatage, "items": {slug: {"rang|sous_type": [n_offres, n_en_ligne, [[prix, rang, vendeur, statut, qte, rep], ...]]}}}
Pour chaque combinaison rang / sous-type presente, on garde les 5 offres les moins cheres
(vendeurs connectes en priorite), ce qui suffit au tableau et aux alertes de l'app.
"""
import gzip
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

WFM = "https://api.warframe.market/v2"
HEADERS = {"Language": "en", "Platform": "pc", "Accept": "application/json",
           "User-Agent": "inventaire-tenno-prix (github.com/thino-yoshi/inventaire-tenno)"}
SPACING = 0.36   # un peu moins de 3 requetes/s, la limite de WFM
TOP = 5

_lock = threading.Lock()
_next = 0.0


def get(url, tries=4):
    global _next
    for attempt in range(tries):
        with _lock:
            start = max(time.time(), _next)
            _next = start + SPACING
        time.sleep(max(0.0, start - time.time()))
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            time.sleep(2 + attempt * 3)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3 + attempt * 5)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    return None


def summarize(orders):
    """{"rang|sous_type": [n_offres, n_en_ligne, top]} a partir des offres de vente."""
    groups = {}
    for o in orders:
        if o.get("type") != "sell" or not o.get("visible", True):
            continue
        key = f"{o.get('rank')}|{o.get('subtype')}"
        groups.setdefault(key, []).append(o)
    out = {}
    for key, sells in groups.items():
        online = [o for o in sells if o["user"].get("status") in ("ingame", "online")]
        pool = sorted(online or sells, key=lambda o: o["platinum"])[:TOP]
        top = [[o["platinum"], o.get("rank"), o["user"].get("ingameName"),
                o["user"].get("status", "offline"), o.get("quantity", 1),
                o["user"].get("reputation", 0)] for o in pool]
        out[key] = [len(sells), len(online), top]
    return out


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "prix.json.gz"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    items = get(f"{WFM}/items")["data"]
    slugs = [it["slug"] for it in items][:limit]
    print(f"{len(slugs)} items a relever", flush=True)
    result, done, t0 = {}, 0, time.time()

    def one(slug):
        data = get(f"{WFM}/orders/item/{slug}")
        return slug, summarize(data["data"]) if data else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        for slug, summary in pool.map(one, slugs):
            done += 1
            if summary:
                result[slug] = summary
            if done % 200 == 0:
                print(f"{done}/{len(slugs)} en {int(time.time() - t0)} s", flush=True)
    payload = {"t": int(time.time()), "items": result}
    with gzip.open(dest, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
    print(f"{len(result)} items ecrits dans {dest} en {int(time.time() - t0)} s")


if __name__ == "__main__":
    main()
