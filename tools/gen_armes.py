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

# --- tables de recompenses du jeu : missions, primes, boss (schema ou pieces de l'arme)
import collections
rewards = load(EXP / "ExportRewards.json")
regions = load(EXP / "ExportRegions.json")
_usage = collections.Counter(i["ItemType"] for v in recipes.values() for i in v.get("ingredients", []))
recettes_de = {}
for rk, rv in recipes.items():
    recettes_de.setdefault(rv.get("resultType"), []).append(rk)


def pieces(ch):  # schema + schemas des pieces propres a l'arme (pas les ressources communes)
    out = {ch}
    for rk in recettes_de.get(ch, []):
        out.add(rk)
        for ing in recipes[rk].get("ingredients", []):
            it = ing["ItemType"]
            if _usage[it] <= 3 and it in recettes_de:
                out.add(it)
                out.update(recettes_de[it])
    return out


noeud_de = {}
for nk, nv in regions.items():
    for m in nv.get("rewardManifests") or []:
        noeud_de.setdefault(m, (f"{fr.get(nv['name'], en.get(nv['name'], nk))} ({fr.get(nv['systemName'], '')})",
                                f"{en.get(nv['name'], nk)} ({en.get(nv['systemName'], '')})"))
FAMILLES = [  # (morceau du chemin de la table, FR, EN) ; les plus precis d'abord
    ("NarmerTable", "Primes Narmer (Cetus ou Fortuna)", "Narmer bounties (Cetus or Fortuna)"),
    ("GhoulBounty", "Primes Ghoul (Cetus)", "Ghoul bounties (Cetus)"),
    ("EidolonJob", "Primes de Cetus (Plaines d'Eidolon)", "Cetus bounties (Plains of Eidolon)"),
    ("VenusJob", "Primes de Fortuna (Plaine d'Orb)", "Fortuna bounties (Orb Vallis)"),
    ("HeistJob", "Braquages de Fortuna", "Fortuna heists"),
    ("DeimosMission", "Primes de Deimos (Faille Cambion)", "Deimos bounties (Cambion Drift)"),
    ("EntratiLabJob", "Primes du Sanctum Anatomica (Deimos)", "Sanctum Anatomica bounties (Deimos)"),
    ("ZarimanJob", "Primes du Zariman", "Zariman bounties"),
    ("DuviriEndlessCircuit", "Le Circuit (Duviri)", "The Circuit (Duviri)"),
    ("DuviriEncounter", "Duviri", "Duviri"),
    ("ArchonSortie", "Chasse aux Archontes", "Archon Hunt"), ("NarmerSortie", "Chasse aux Archontes", "Archon Hunt"),
    ("SortieRewards", "Sorties", "Sorties"),
    ("VoidStorm", "Tempêtes du Néant (Railjack)", "Void Storms (Railjack)"),
    ("RailjackMission", "Missions Railjack", "Railjack missions"),
    ("1999", "Höllvania (1999)", "Höllvania (1999)"),
    ("Kuva", "Forteresse Kuva / missions Kuva", "Kuva Fortress / Kuva missions"),
    ("Derelict", "Derelict Orokin", "Orokin Derelict"), ("VaultRewards", "Coffres Orokin (Derelict)", "Orokin Vaults (Derelict)"),
    ("InvasionRewards", "Invasions", "Invasions"), ("Ropalolyst", "Le Ropalolyst (Jupiter)", "The Ropalolyst (Jupiter)"),
    ("GolemMission", "Golem Jordas (Éris)", "Jordas Golem (Eris)"), ("Ascension", "Ascension", "Ascension"),
    ("TauMission", "Système Tau", "Tau system"), ("NokkoColony", "Les Tréfonds (Fortuna)", "Deepmines (Fortuna)"),
    ("FiveFates", "Koumei (Cetus)", "Koumei (Cetus)"), ("DisruptionMission", "Missions Perturbation", "Disruption missions"),
    ("ShadowgrapherMission", "Vesper Relay (Follie)", "Vesper Relay (Follie)"), ("DagathMission", "Dagath", "Dagath"),
    ("JadeShadowsConstellations", "Les Ombres de Jade : Constellations", "Jade Shadows: Constellations"),
    ("EventMission", "Événement (temporaire)", "Event (limited time)"),
]


def source_recompense(ch):
    trouve = []
    p = pieces(ch)
    for mk, mv in rewards.items():
        if "/CacheRewards" in mk or not any(e["type"].replace("/StoreItems", "") in p for rot in mv for e in rot):
            continue
        lab = noeud_de.get(mk) or next(((f, e) for m, f, e in FAMILLES if m in mk), None)
        if lab and lab not in trouve:
            trouve.append(lab)
    trouve.sort(key=lambda x: "temporaire" in x[0])  # evenements en dernier
    return [list(x) for x in trouve[:3]]


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
evenement = lambda chemin: "Events/" in chemin and "RadioLegion" not in chemin  # Nightwave = permanent
for vk, vv in vendors.items():
    lab = next(((f, e) for m, f, e in MARCHANDS if m in vk), None)
    if not lab:
        continue
    for it in vv.get("items", []):
        t = cible(it["storeItem"].replace("/StoreItems", ""))
        if t and (t not in vend_de or evenement(vend_src[t])) and not (t in vend_de and evenement(vk)):
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
TESHIN = ["Grotte de Teshin (Duviri), contre des Pathos Clamps", "Teshin's Cave (Duviri), for Pathos Clamps"]
GHOUL = ["Ghouls Auger et Devourer (primes Ghoul, Plaines d'Eidolon)", "Ghoul Augers and Devourers (Ghoul bounties, Plains of Eidolon)"]
MELICA = ["Cephalon Melica (Chrysalith, Zariman), contre du Kuva Entropique", "Cephalon Melica (Chrysalith, Zariman), for Entropic Kuva"]
PERTURB_UR = ["Démolisseurs en Perturbation sur Ur (Uranus)", "Demolishers in Disruption on Ur (Uranus)"]
MANUEL = {"Thornbak": ["Quête Le Professeur", "Quest The Teacher"], "Skiajati": ["Quête Le Sacrifice", "Quest The Sacrifice"],
          "Dread": STALKER, "Despair": STALKER, "Hate": STALKER,
          "Brakk": ["Les Trois Grustrag (attaque pendant les missions)", "The Grustrag Three (attack during missions)"],
          "Seer": ["Capitaine Vor (Tolstoj, Mercure)", "Captain Vor (Tolstoj, Mercury)"],
          "Azima": TRIBUT, "Zenistar": TRIBUT, "Zenith": TRIBUT, "Sigma & Octantis": TRIBUT,
          "Laetum": ZARIMAN, "Phenmor": ZARIMAN, "Felarx": ZARIMAN, "Innodem": ZARIMAN, "Praedos": ZARIMAN,
          # verifiees une par une (wiki officiel et guides, novembre 2026)
          "Cronus": ["Capitaine Vor (Tolstoj, Mercure) : schéma garanti", "Captain Vor (Tolstoj, Mercury): guaranteed blueprint"],
          "Ether Daggers": ["Quête Rêves Volés, puis Cephalon Simaris", "Quest Stolen Dreams, then Cephalon Simaris"],
          "Wolf Sledge": ["Le Loup de Saturne Six", "The Wolf of Saturn Six"],
          "Sporothrix": ["Loid, Necraloid (Deimos) · Coffres d'Isolement", "Loid, Necraloid (Deimos) · Isolation Vaults"],
          "Arum Spinosa": ["Coffres d'Isolement Arcana (Faille Cambion, Deimos)", "Arcana Isolation Vaults (Cambion Drift, Deimos)"],
          "Pathocyst": ["Zealoid Prelate (Assassinat du Derelict, Deimos)", "Zealoid Prelate (Derelict Assassination, Deimos)"],
          "Vitrica": ["Nihil : son Oubliette et sa clé sont dans la boutique Nightwave", "Nihil: his Oubliette and key are in the Nightwave shop"],
          "War": ["Le Stalker (après Le Second Rêve) · demande War Brisée", "The Stalker (after The Second Dream) · needs Broken War"],
          "Broken War": ["Quête Le Second Rêve · puis le Stalker", "Quest The Second Dream · then the Stalker"],
          "Braton": ["Arme de départ (Éveil) ou Marché", "Starter weapon (Awakening) or Market"],
          "Lato": ["Arme de départ (Éveil) ou Marché", "Starter weapon (Awakening) or Market"],
          "Aklato": ["Marché (fabriqué avec deux Lato)", "Market (built from two Lato)"],
          "Strun": ["Marché", "Market"], "Lex": ["Marché", "Market"], "Sicarus": ["Marché", "Market"],
          "Gorgon": ["Marché", "Market"], "Machete": ["Marché", "Market"],
          "Carmine Penta": ["Railjack : Derelict Freightlinkers (Pluton et Voile Proxima)", "Railjack: Derelict Freightlinkers (Pluto and Veil Proxima)"],
          "Ax-52": ["Syndicat The Hex (rang 4, 30 000 de réputation)", "The Hex syndicate (rank 4, 30,000 standing)"],
          "Shedu": ["Quête Erra (schéma) · pièces sur les Symbilysts", "Quest Erra (blueprint) · parts from Symbilysts"],
          "Haalvu": ["Récompense Twitch (TennoCon 2026)", "Twitch drop (TennoCon 2026)"],
          "Trumna": ["Loid, Necraloid rang 3 (Deimos)", "Loid, Necraloid rank 3 (Deimos)"],
          "Sepulcrum": ["Loid, Necraloid rang 2 (Deimos)", "Loid, Necraloid rank 2 (Deimos)"],
          "Vastilok": ["Baro Ki'Teer", "Baro Ki'Teer"], "Zylok": ["Baro Ki'Teer", "Baro Ki'Teer"],
          "Mara Detron": ["Baro Ki'Teer", "Baro Ki'Teer"],
          "Athodai": ["Railjack : missions de Vénus Proxima", "Railjack: Venus Proxima missions"],
          "Detron": ["Chasseur Zanuka (apparaît pendant les missions Corpus)", "Zanuka Hunter (shows up during Corpus missions)"],
          "Grimoire": ["Quête Murmures dans les murs, puis Bird 3 (Sanctum Anatomica)", "Quest Whispers in the Walls, then Bird 3 (Sanctum Anatomica)"],
          "Argo & Vel": TESHIN, "Syam": TESHIN, "Sampotes": TESHIN, "Azothane": TESHIN, "Edun": TESHIN,
          "Tak & Lug": GHOUL, "Tonkkatt": GHOUL,
          "Keratinos": ["Father, Nécralisk (réputation Entrati)", "Father, Necralisk (Entrati standing)"],
          "Stropha": ["Le Chacal (Fossa, Vénus) · pièces dans le Néant Granum", "The Jackal (Fossa, Venus) · parts in the Granum Void"],
          "Stahlta": ["Néant Granum (Corpus)", "Granum Void (Corpus)"],
          "Pennant": ["Missions Railjack", "Railjack missions"],
          "Acceltra": PERTURB_UR, "Akarius": PERTURB_UR,
          "Cinta": ["Énigmes de Duviri (après Le Paradoxe Duviri)", "Duviri Enigmas (after The Duviri Paradox)"],
          "Quellor": ["Railjack : capitaines Corpus et Grineer", "Railjack: Corpus and Grineer captains"],
          "Kompressa": ["Ventkids, rang 5 (Fortuna)", "Ventkids, rank 5 (Fortuna)"],
          "Zymos": ["Loid, Necraloid rang 1 (Deimos)", "Loid, Necraloid rank 1 (Deimos)"],
          "Simulor": ["Cephalon Simaris (75 000 de réputation)", "Cephalon Simaris (75,000 standing)"],
          "Korrudo": ["Tusk Thumper Bull et Doma (Plaines d'Eidolon)", "Tusk Thumper Bull and Doma (Plains of Eidolon)"],
          "Twin Kohmak": ["Kela De Thaym (Merrow, Sedna)", "Kela De Thaym (Merrow, Sedna)"],
          "Nunchasa": MELICA, "Aksondol": MELICA,
          "Vericres": ["Baro Ki'Teer", "Baro Ki'Teer"]}


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
    plat = bool(i.get("marketCost")) and not i.get("isPrime") and not i.get("bpCost")
    if chemin in vend_de and (not s or not evenement(vend_src.get(chemin, ""))):
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
    if not s or s == [list(vend_de.get(chemin, ("", "")))]:
        s += [x for x in source_recompense(chemin) if x not in s]
    if plat:
        s.append(["Aussi en vente au Marché contre du platine", "Also sold in the Market for platinum"])
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
        if not s or all("platine" in x[0] for x in s):
            sans.append(i["name"])
        tfr, ten = TYPES[i["type"]]
        armes.append({"id": chemin, "fr": nfr.title() if nfr.isupper() else nfr, "en": nen.title() if nen.isupper() else nen,
                      "c": cat, "tfr": tfr, "ten": ten, "mr": i.get("masteryReq") or 0,
                      "s": s, "w": i.get("wikiaUrl") or "",
                      "lien": all("platine" in x[0] for x in s)})

armes.sort(key=lambda a: (a["c"], a["mr"], a["en"].lower()))
OUT.write_text(json.dumps(armes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(armes)} armes -> {OUT} ({OUT.stat().st_size // 1024} Ko) ; sans source connue : {len(sans)}")
print(", ".join(sans))
