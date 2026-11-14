"""Construit assets/armes.json : toutes les armes (principales, secondaires, mêlée) avec leur rang de
maîtrise et où les obtenir, pour l'onglet Info / aide → Débuter → Armes.

A relancer apres une grosse mise a jour du jeu :
    python tools/gen_armes.py CHEMIN/warframe-public-export-plus CHEMIN/warframe-items/data/json

Sources : warframe-public-export-plus (noms officiels FR/EN, Dojo, marchands, recompenses de quetes)
et WFCD warframe-items (marche, butins, Prime, liens wiki).
"""
import json
import sys
from pathlib import Path

EXP, WFCD = Path(sys.argv[1]), Path(sys.argv[2])
OUT = Path(__file__).resolve().parent.parent / "assets" / "armes.json"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


w = load(EXP / "ExportWeapons.json")
recipes = load(EXP / "ExportRecipes.json")
dojo = load(EXP / "ExportDojoRecipes.json")
vendors = load(EXP / "ExportVendors.json")
keys = load(EXP / "ExportKeys.json")
fr, en = load(EXP / "dict.fr.json"), load(EXP / "dict.en.json")

res_of = {k: v.get("resultType") for k, v in recipes.items()}
cible = lambda p: p if p in w else res_of.get(p)  # arme, ou arme fabriquee par ce schema

# --- Dojo
dojo_armes = set()
for rk, rv in dojo.get("research", {}).items():
    t = cible(rk) or cible(rv.get("resultType", ""))
    if t:
        dojo_armes.add(t)

# --- quetes qui donnent l'arme (ou son schema)
quete_de = {}
for kp, kv in keys.items():
    for st in kv.get("chainStages") or []:
        m = st.get("messageToSendWhenTriggered") or {}
        for att in (m.get("attachments") or []) + (st.get("itemsToGiveWhenTriggered") or []):
            t = cible(att)
            if t and t not in quete_de:
                quete_de[t] = (fr.get(kv["name"], en.get(kv["name"], "")), en.get(kv["name"], ""))

# --- marchands (hors evenements temporaires)
MARCHANDS = [  # (morceau du chemin, FR, EN)
    ("Deimos/Otak", "Otak, Nécralisk (Deimos)", "Otak, Necralisk (Deimos)"),
    ("Duviri/Acrithis", "Acrithis (Duviri)", "Acrithis (Duviri)"),
    ("EntratiLabs/", "Sanctum Anatomica (Deimos)", "Sanctum Anatomica (Deimos)"),
    ("AscensionVendor", "Ascension (Ordis)", "Ascension (Ordis)"),
    ("AspirantZorba", "Aspirant Zorba, Vesper Relay (Vénus)", "Aspirant Zorba, Vesper Relay (Venus)"),
    ("HunhowVendor", "Hunhow (Les Ombres de Jade)", "Hunhow (Jade Shadows)"),
    ("PerrinSequenceWeaponVendor", "Ergo Glast, relais (armes Tenet)", "Ergo Glast, relays (Tenet weapons)"),
    ("Kahl/Chipper", "Chipper, Garnison de Kahl", "Chipper, Kahl's Garrison"),
    ("Ostron/FiveFates", "Sanctuaire de Koumei (Cetus)", "Koumei's Shrine (Cetus)"),
    ("Tau/Prequel/TriadPity", "L'Ancienne Paix (Tau)", "The Old Peace (Tau)"),
    ("TheHex/HexScaldran", "Höllvania (Scaldra)", "Höllvania (Scaldra)"),
    ("TheHex/InfestedLich", "Höllvania (armes Coda)", "Höllvania (Coda weapons)"),
    ("TheHex/Temple1999", "Höllvania", "Höllvania"),
    ("Zariman/Archimedean", "Chrysalith (Zariman)", "Chrysalith (Zariman)"),
    ("RadioLegion", "Boutique Nightwave (Intermission)", "Nightwave shop (Intermission)"),
    ("Events/", "Boutique d'événement (temporaire)", "Event shop (limited time)"),
]
vend_de, vend_src = {}, {}
for vk, vv in vendors.items():
    lab = next(((f, e) for m, f, e in MARCHANDS if m in vk), None)
    if not lab:
        continue
    for it in vv.get("items", []):
        t = cible(it["storeItem"].replace("/StoreItems", ""))
        if t and (t not in vend_de or "Events/" in vend_src[t]) and not (t in vend_de and "Events/" in vk):
            vend_de[t], vend_src[t] = lab, vk

TYPES = {"Rifle": ("Fusil", "Rifle"), "Shotgun": ("Fusil à pompe", "Shotgun"), "Bow": ("Arc", "Bow"),
         "Sniper": ("Fusil de précision", "Sniper rifle"), "Launcher": ("Lanceur", "Launcher"),
         "Pistol": ("Pistolet", "Pistol"), "Dual Pistols": ("Pistolets doubles", "Dual pistols"),
         "Throwing": ("Arme de lancer", "Throwing weapon"), "Melee": ("Mêlée", "Melee")}
CATS = {"Primary": "primaire", "Secondary": "secondaire", "Melee": "melee"}


# sources sures ajoutees a la main (absentes des donnees)
STALKER = ["Le Stalker (il apparaît pendant les missions)", "The Stalker (shows up during missions)"]
TRIBUT = ["Tribut quotidien (récompense de connexion)", "Daily Tribute (login milestone)"]
ZARIMAN = ["Zariman (Holdfasts)", "Zariman (Holdfasts)"]
MANUEL = {"Thornbak": ["Quête Le Professeur", "Quest The Teacher"], "Skiajati": ["Quête Le Sacrifice", "Quest The Sacrifice"],
          "Dread": STALKER, "Despair": STALKER, "Hate": STALKER,
          "Brakk": ["Les Trois Grustrag (attaque pendant les missions)", "The Grustrag Three (attack during missions)"],
          "Seer": ["Capitaine Vor (Tolstoj, Mercure)", "Captain Vor (Tolstoj, Mercury)"],
          "Azima": TRIBUT, "Zenistar": TRIBUT, "Zenith": TRIBUT, "Sigma & Octantis": TRIBUT,
          "Laetum": ZARIMAN, "Phenmor": ZARIMAN, "Felarx": ZARIMAN, "Innodem": ZARIMAN, "Praedos": ZARIMAN}


SYNDICATS = {"Steel Meridian", "Arbiters of Hexis", "Cephalon Suda", "The Perrin Sequence", "Red Veil", "New Loka"}
RANGS = {"General": "Général", "Exalted": "Exalté", "Maxim": "Maxime", "Genius": "Génie", "Partner": "Partenaire",
         "Executive": "Dirigeant", "Exalted": "Exalté", "Hallowed": "Sacré", "Visionary": "Visionnaire"}


def sources(i, chemin):
    nom = i["name"]
    s = [list(MANUEL[nom])] if nom in MANUEL else []
    q = quete_de.get(chemin)
    if q:
        s.append([f"Quête {q[0]}", f"Quest {q[1]}"])
    if i.get("isPrime"):
        s.append(["Reliques du Néant (Prime)" + (" · retirée, voir Varzia" if i.get("vaulted") else ""),
                  "Void Relics (Prime)" + (" · vaulted, see Varzia" if i.get("vaulted") else "")])
    if nom.startswith("Kuva "):
        s.append(["Liche Kuva (après La Guerre Intérieure)", "Kuva Lich (after The War Within)"])
    elif nom.startswith("Tenet ") and chemin not in vend_de:
        s.append(["Sœur de Parvos (Tenet)", "Sister of Parvos (Tenet)"])
    elif nom.startswith("Coda ") and chemin not in vend_de:
        s.append(["Liche Infestée (Coda)", "Infested Lich (Coda)"])
    elif nom.startswith("Prisma "):
        s.append(["Baro Ki'Teer", "Baro Ki'Teer"])
    if chemin in dojo_armes:
        s.append(["Dojo de clan (recherche)", "Clan Dojo (research)"])
    if i.get("bpCost") and chemin not in dojo_armes:  # pour le Dojo, bpCost = prix de reproduction
        c = f"{i['bpCost']:,}".replace(",", " ")
        s.append([f"Marché : schéma pour {c} crédits", f"Market: blueprint for {i['bpCost']:,} credits"])
    elif i.get("marketCost") and not i.get("isPrime"):
        s.append(["Marché (platine uniquement)", "Market (platinum only)"])
    if chemin in vend_de and (not s or "Events/" not in vend_src.get(chemin, "")):
        s.append(list(vend_de[chemin]))
    drops = [d for c in (i.get("components") or []) for d in (c.get("drops") or [])] + (i.get("drops") or [])
    drops = [d for d in drops if "Skin" not in (d.get("type") or "") and "Conclave" not in d.get("location", "")]
    if drops and not i.get("isPrime"):
        best = max(drops, key=lambda d: d.get("chance") or 0)
        loc = best["location"]
        synd, _, rang = loc.partition(", ")
        if synd in SYNDICATS and rang:
            s.append([f"Syndicat {synd} (rang {RANGS.get(rang, rang)})", f"{synd} syndicate ({rang} rank)"])
        else:
            s.append([loc, loc])
    if not s and (nom.endswith(" Vandal") or nom.endswith(" Wraith")):
        s.append(["Variante rare : invasions, événements ou Baro Ki'Teer", "Rare variant: invasions, events or Baro Ki'Teer"])
    if not s and nom.startswith("Dex "):
        s.append(["Cadeau d'anniversaire de Warframe (connexion)", "Warframe anniversary gift (login)"])
    if not s and nom.startswith("Mk1-"):
        s.append(["Marché (crédits)", "Market (credits)"])
    return s


armes = []
sans = []
for fichier, cat in CATS.items():
    for i in load(WFCD / f"{fichier}.json"):
        if not i.get("masterable") or i.get("type") not in TYPES or (cat == "melee" and i.get("type") != "Melee"):
            continue
        chemin = i["uniqueName"]
        e = w.get(chemin) or {}
        nfr = fr.get(e.get("name", ""), "") or i["name"]
        nen = en.get(e.get("name", ""), "") or i["name"]
        s = sources(i, chemin)
        if not s:
            sans.append(i["name"])
        tfr, ten = TYPES[i["type"]]
        armes.append({"id": chemin, "fr": nfr.title() if nfr.isupper() else nfr, "en": nen.title() if nen.isupper() else nen,
                      "c": cat, "tfr": tfr, "ten": ten, "mr": i.get("masteryReq") or 0,
                      "s": s, "w": i.get("wikiaUrl") or ""})

armes.sort(key=lambda a: (a["c"], a["mr"], a["en"].lower()))
OUT.write_text(json.dumps(armes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(armes)} armes -> {OUT} ({OUT.stat().st_size // 1024} Ko) ; sans source connue : {len(sans)}")
print(", ".join(sans))
