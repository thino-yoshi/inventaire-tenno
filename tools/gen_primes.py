"""Construit assets/primes.json : tous les objets Prime (Warframes, armes, compagnons, Archwings) avec
leurs pieces et les reliques ou les trouver, pour l'onglet Info / aide -> Prime.

A relancer apres une nouvelle Prime Access ou un retrait de reliques :
    python tools/gen_primes.py CHEMIN/warframe-public-export-plus CHEMIN/warframe-items/data/json
"""
import json
import sys
from pathlib import Path

EXP, WFCD = Path(sys.argv[1]), Path(sys.argv[2])
OUT = Path(__file__).resolve().parent.parent / "assets" / "primes.json"
load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))

res, recipes = load(EXP / "ExportResources.json"), load(EXP / "ExportRecipes.json")
relics, rewards = load(EXP / "ExportRelics.json"), load(EXP / "ExportRewards.json")
weapons, frames = load(EXP / "ExportWeapons.json"), load(EXP / "ExportWarframes.json")
sentinels = load(EXP / "ExportSentinels.json")
fr, en = load(EXP / "dict.fr.json"), load(EXP / "dict.en.json")

# --- piece -> reliques (version intacte de chaque relique ; rarete de la piece dans la relique)
RARETE = {"COMMON": 0, "UNCOMMON": 1, "RARE": 2}
reliques_de = {}
for rp, rv in relics.items():
    if rv.get("quality") != "VPQ_BRONZE" or rv.get("era") in ("Requiem",):
        continue
    nom = f"{rv['era']} {rv['category']}"
    for rot in rewards.get(rv.get("rewardManifest"), []):
        for e in rot:
            piece = e["type"].replace("/StoreItems", "")
            reliques_de.setdefault(piece, []).append((nom, RARETE.get(e.get("rarity"), 0)))

# une piece de Warframe tombe sous forme de schema (ex. RhinoPrimeChassisBlueprint -> RhinoPrimeChassisComponent)
schema_de = {}
for rk, rv in recipes.items():
    if rv.get("resultType") in res and rk in reliques_de:
        reliques_de.setdefault(rv["resultType"], []).extend(reliques_de[rk])
        schema_de[rv["resultType"]] = rk

# reliques qui tombent actuellement en mission (WFCD : "vaulted" = False)
ACTIVES = {r["name"].replace(" Intact", "") for r in load(WFCD / "Relics.json")
           if r["name"].endswith(" Intact") and not r.get("vaulted")}

# nom de l'objet fabrique par chaque schema (pour retrouver les pieces)
CATS = {"Warframes": "warframe", "Primary": "primaire", "Secondary": "secondaire", "Melee": "melee",
        "Sentinels": "compagnon", "Pets": "compagnon", "SentinelWeapons": "compagnon",
        "Archwing": "archwing", "Arch-Gun": "archwing", "Arch-Melee": "archwing"}
PARTIES = {"Blueprint": ("Schéma", "Blueprint")}


def nom_objet(chemin, i):
    for sect in (frames, weapons, sentinels):
        e = sect.get(chemin)
        if e:
            return (fr.get(e["name"], i["name"]).replace("<ARCHWING> ", ""),
                    en.get(e["name"], i["name"]).replace("<ARCHWING> ", ""))
    return i["name"], i["name"]


def nom_piece(chemin, objet_fr, objet_en):
    e = res.get(chemin)
    if e:  # ex. "Rhino Prime - Châssis" -> "Châssis"
        f, n = fr.get(e["name"], ""), en.get(e["name"], "")
        return (f.split(" - ")[-1] if f else n.split(" ")[-1]), (n.replace(objet_en, "").strip(" -") or n)
    if chemin.endswith("Blueprint"):
        return PARTIES["Blueprint"]
    return chemin.rsplit("/", 1)[-1], chemin.rsplit("/", 1)[-1]


primes = []
for fichier, cat in CATS.items():
    f = WFCD / f"{fichier}.json"
    if not f.exists():
        continue
    for i in load(f):
        if not i.get("isPrime") or not i.get("masterable", True):
            continue
        chemin = i["uniqueName"]
        nfr, nen = nom_objet(chemin, i)
        pieces = []
        for c in i.get("components") or []:
            pc = c["uniqueName"]
            if pc not in reliques_de and "Prime" not in pc:
                continue  # ressource commune (Orokin Cell...) ou arme de base demandee
            if pc in weapons:  # arme Prime demandee entiere (ex. 2 Bronco Prime pour l'Akbronco Prime)
                wf, we = nom_objet(pc, {"name": pc.rsplit("/", 1)[-1]})
                deja = next((x for x in pieces if x.get("arme") and x["en"] == we), None)
                if deja:
                    deja["n"] += c.get("itemCount", 1)
                else:
                    pieces.append({"fr": wf, "en": we, "n": c.get("itemCount", 1), "r": [], "arme": True})
                continue
            pfr, pen = nom_piece(pc, nfr, nen)
            rel = sorted(set(reliques_de.get(pc, [])), key=lambda x: (["Lith", "Meso", "Neo", "Axi"].index(x[0].split()[0])
                                                                          if x[0].split()[0] in ("Lith", "Meso", "Neo", "Axi") else 9, x[0]))
            rel.sort(key=lambda x: x[0] not in ACTIVES)  # celles qui tombent en ce moment d'abord
            pieces.append({"fr": pfr, "en": pen, "n": c.get("itemCount", 1), "id": [pc] + ([schema_de[pc]] if pc in schema_de else []),
                           "r": [[n, r, int(n in ACTIVES)] for n, r in rel]})
        primes.append({"id": chemin, "fr": nfr, "en": nen, "c": cat, "mr": i.get("masteryReq") or 0,
                       "retire": bool(i.get("vaulted")), "p": pieces, "w": i.get("wikiaUrl") or ""})

# --- Primes hors reliques (verifie nov. 2026) et Prime Access trop recente pour l'export
FONDATEURS = ("Exclusif aux packs Fondateurs (2012-2013) : plus obtenable.",
              "Founders pack exclusive (2012-2013): no longer obtainable.")
AVEC = lambda s: (f"Livrée avec {s} : pas de pièces à farmer.", f"Comes with {s}: no parts to farm.")
SOURCE = {
    "Excalibur Prime": FONDATEURS, "Lato Prime": FONDATEURS, "Skana Prime": FONDATEURS,
    "Gotva Prime": ("Vendue de temps en temps par Baro Ki'Teer (ducats + crédits). D'abord offerte en Twitch drop à la TennoCon 2023.",
                    "Sold from time to time by Baro Ki'Teer (ducats + credits). First given as a TennoCon 2023 Twitch drop."),
    "War Prime": ("Construire « Babiole de Hunhow » : schéma vendu par Hunhow à la Tour Pontis contre des Talents (après la quête Jade Shadows : Constellations).",
                  "Build Hunhow's Trinket: blueprint sold by Hunhow at Pontis Tower for Talents (after Jade Shadows: Constellations)."),
    "Burst Laser Prime": AVEC("Shade Prime"), "Deconstructor Prime": AVEC("Helios Prime"),
    "Deth Machine Rifle Prime": AVEC("Dethcube Prime"), "Prime Laser Rifle": AVEC("Wyrm Prime"),
    "Sweeper Prime": AVEC("Carrier Prime"), "Verglas Prime": AVEC("Nautilus Prime"),
}
# Prime Access Citrine (23 sept. 2026) : tables de drop PC du 22 oct. 2026
P = lambda f, e, rel, r: {"fr": f, "en": e, "n": 1, "r": [[rel, r, int(rel in ACTIVES)]]}
RELIQUES = {
    "Citrine Prime": [P("Schéma", "Blueprint", "Meso C11", 2), P("Châssis", "Chassis", "Neo C11", 0),
                      P("Neuroptiques", "Neuroptics", "Axi C12", 2), P("Systèmes", "Systems", "Lith S19", 1)],
    "Steflos Prime": [P("Schéma", "Blueprint", "Neo Y2", 1), P("Canon", "Barrel", "Axi S21", 2),
                      P("Culasse", "Receiver", "Lith A13", 0), P("Crosse", "Stock", "Meso D9", 1)],
    "Corufell Prime": [P("Schéma", "Blueprint", "Meso V17", 0), P("Poignée", "Handle", "Axi C12", 1),
                       P("Culasse", "Receiver", "Neo C10", 2), P("Crosse", "Stock", "Lith C15", 0)],
}
ordre = lambda x: (x.get("arme", False), x["en"] != "Blueprint", x["fr"])  # Schema d'abord
for a in primes:
    a["p"].sort(key=ordre)
    if any(r[2] for x in a["p"] for r in x["r"]):
        a["retire"] = False
    elif a["p"]:
        a["retire"] = True
    if a["en"] in RELIQUES:
        a["p"], a["retire"] = RELIQUES[a["en"]], False
    if a["en"] in SOURCE:
        a["s"], a["p"], a["retire"] = SOURCE[a["en"]], [], False

primes.sort(key=lambda a: (a["c"], a["en"].lower()))
OUT.write_text(json.dumps(primes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
sans = [p["en"] for p in primes if not any(x["r"] for x in p["p"]) and "s" not in p]
print(f"{len(primes)} objets Prime -> {OUT} ({OUT.stat().st_size // 1024} Ko) ; sans relique connue : {len(sans)}")
print(", ".join(sans))
