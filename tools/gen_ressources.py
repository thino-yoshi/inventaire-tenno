"""Construit assets/ressources.json : les ressources planete par planete, pour l'onglet Info / aide -> Ressources.

- ressources de region (ce que tombent les ennemis et caisses de chaque planete) : table du wiki officiel
  (Region Resource), verifiee en novembre 2026 ; meilleurs noeuds : page Resources du wiki ;
- mondes ouverts (minerais, gemmes, plantes, poissons) : base WFCD (champ description "Location: ...") ;
- noms francais / anglais : fichiers du jeu (warframe-public-export-plus).

    python tools/gen_ressources.py CHEMIN/warframe-public-export-plus CHEMIN/warframe-items/data/json
"""
import json
import re
import sys
from pathlib import Path

EXP, WFCD = Path(sys.argv[1]), Path(sys.argv[2])
OUT = Path(__file__).resolve().parent.parent / "assets" / "ressources.json"
load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
res = load(EXP / "ExportResources.json")
fr, en = load(EXP / "dict.fr.json"), load(EXP / "dict.en.json")

par_nom = {}
for k, v in res.items():
    par_nom.setdefault(en.get(v.get("name", ""), ""), k)
# variantes de quete / tutoriel : on garde le chemin "normal"
par_nom.update({"Polymer Bundle": "/Lotus/Types/Items/MiscItems/PolymerBundle",
                "Salvage": "/Lotus/Types/Items/MiscItems/Salvage",
                "Entrati Lanthorn": "/Lotus/Types/Gameplay/Zariman/Resources/ZarimanMiscItemB"})


def noms(chemin, defaut=""):
    e = res.get(chemin) or {}
    return fr.get(e.get("name", ""), defaut) or defaut, en.get(e.get("name", ""), defaut) or defaut


# rarete generale de chaque ressource de region
RARETE = {"Ferrite": 0, "Alloy Plate": 0, "Salvage": 0, "Nano Spores": 0, "Polymer Bundle": 0,
          "Circuits": 1, "Plastids": 1, "Rubedo": 1, "Hexenon": 1, "Voidgel Orb": 1,
          "Detonite Ampule": 3, "Fieldron Sample": 3, "Mutagen Sample": 3,
          "Morphics": 2, "Neurodes": 2, "Orokin Cell": 2, "Gallium": 2, "Control Module": 2,
          "Neural Sensors": 2, "Argon Crystal": 2, "Entrati Lanthorn": 2}

# ressources de region par planete (wiki officiel, Region Resource)
PLANETES = [
    ("Mercury", ["Ferrite", "Polymer Bundle", "Detonite Ampule", "Morphics"]),
    ("Venus", ["Alloy Plate", "Polymer Bundle", "Circuits", "Fieldron Sample"]),
    ("Earth", ["Ferrite", "Rubedo", "Detonite Ampule", "Neurodes"]),
    ("Lua", ["Ferrite", "Rubedo", "Detonite Ampule", "Neurodes"]),
    ("Mars", ["Salvage", "Fieldron Sample", "Morphics", "Gallium"]),
    ("Phobos", ["Alloy Plate", "Rubedo", "Plastids", "Morphics"]),
    ("Deimos", ["Nano Spores", "Mutagen Sample", "Orokin Cell", "Neurodes"]),
    ("Ceres", ["Alloy Plate", "Circuits", "Detonite Ampule", "Orokin Cell"]),
    ("Jupiter", ["Salvage", "Alloy Plate", "Hexenon", "Neural Sensors"]),
    ("Europa", ["Rubedo", "Fieldron Sample", "Morphics", "Control Module"]),
    ("Saturn", ["Nano Spores", "Plastids", "Detonite Ampule", "Orokin Cell"]),
    ("Uranus", ["Polymer Bundle", "Plastids", "Detonite Ampule", "Gallium"]),
    ("Neptune", ["Nano Spores", "Ferrite", "Fieldron Sample", "Control Module"]),
    ("Pluto", ["Alloy Plate", "Rubedo", "Plastids", "Fieldron Sample", "Morphics"]),
    ("Sedna", ["Alloy Plate", "Salvage", "Rubedo", "Detonite Ampule"]),
    ("Eris", ["Nano Spores", "Plastids", "Mutagen Sample", "Neurodes"]),
    ("Void", ["Ferrite", "Rubedo", "Argon Crystal", "Control Module"]),
    ("Kuva Fortress", ["Salvage", "Circuits", "Detonite Ampule", "Neural Sensors"]),
    ("Zariman", ["Alloy Plate", "Ferrite", "Voidgel Orb", "Entrati Lanthorn"]),
]
SYSTEMES = {"Mercury": "Mercury", "Venus": "Venus", "Earth": "Earth", "Lua": "Moon", "Mars": "Mars", "Phobos": "Phobos",
            "Ceres": "Ceres", "Jupiter": "Jupiter", "Europa": "Europa", "Saturn": "Saturn", "Uranus": "Uranus",
            "Neptune": "Neptune", "Pluto": "Pluto", "Sedna": "Sedna", "Eris": "Eris", "Void": "Void",
            "Kuva Fortress": "Fortress"}


def nom_planete(p):
    if p == "Deimos":
        k = "/Lotus/Language/InfestedMicroplanet/SolarMapDeimosName"
    elif p == "Zariman":
        k = "/Lotus/Language/Zariman/ZarimanRegionName"
    else:
        k = f"/Lotus/Language/Locations/{SYSTEMES[p]}"
    return fr.get(k, p), en.get(k, p)


# meilleurs endroits (wiki officiel, page Resources -> Recommended Farming Locations, et guides)
MEILLEUR = {
    "Ferrite": ("Tikal (Terre), Apollodorus (Mercure) ou n'importe quelle Survie / Défense du Néant",
                "Tikal (Earth), Apollodorus (Mercury) or any Void Survival / Defense"),
    "Alloy Plate": ("Gabii ou Draco (Cérès), Malva (Vénus)", "Gabii or Draco (Ceres), Malva (Venus)"),
    "Circuits": ("Draco (Cérès) ou Malva (Vénus)", "Draco (Ceres) or Malva (Venus)"),
    "Salvage": ("Cameria (Jupiter) ou Wahiba (Mars)", "Cameria (Jupiter) or Wahiba (Mars)"),
    "Hexenon": ("Ganymède ou Cameria (Jupiter)", "Ganymede or Cameria (Jupiter)"),
    "Morphics": ("Wahiba (Mars), Capitaine Vor (Tolstoj, Mercure) ou Apollodorus (Mercure)",
                 "Wahiba (Mars), Captain Vor (Tolstoj, Mercury) or Apollodorus (Mercury)"),
    "Neural Sensors": ("Cameria (Jupiter), Alad V (Themisto, Jupiter) ou Taveuni (Forteresse Kuva)",
                       "Cameria (Jupiter), Alad V (Themisto, Jupiter) or Taveuni (Kuva Fortress)"),
    "Orokin Cell": ("Terrorem (Deimos), Draco (Cérès) ou Piscinas (Saturne)", "Terrorem (Deimos), Draco (Ceres) or Piscinas (Saturn)"),
    "Plastids": ("Zeugma (Phobos), Ophelia (Uranus) ou Zabala (Éris)", "Zeugma (Phobos), Ophelia (Uranus) or Zabala (Eris)"),
    "Polymer Bundle": ("Ophelia ou Assur (Uranus), Apollodorus (Mercure)", "Ophelia or Assur (Uranus), Apollodorus (Mercury)"),
    "Rubedo": ("Stickney (Phobos) ou Palus (Pluton)", "Stickney (Phobos) or Palus (Pluto)"),
    "Control Module": ("Mot (Néant)", "Mot (Void)"),
    "Gallium": ("Assur (Uranus)", "Assur (Uranus)"),
    "Nano Spores": ("Terrorem (Deimos)", "Terrorem (Deimos)"),
    "Mutagen Sample": ("Terrorem (Deimos)", "Terrorem (Deimos)"),
    "Fieldron Sample": ("Galatea (Neptune)", "Galatea (Neptune)"),
    "Detonite Ampule": ("Draco (Cérès)", "Draco (Ceres)"),
    "Neurodes": ("Lua", "Lua"),
}
NOTES = {
    "Argon Crystal": ("Disparaît petit à petit : chaque jour, une partie de ton stock s'évapore. Ne farme que ce qu'il te faut.",
                      "Decays: every day part of your stock disappears. Only farm what you need."),
    "Detonite Ampule": ("Ressource de recherche (Dojo de clan).", "Research resource (clan Dojo)."),
    "Fieldron Sample": ("Ressource de recherche (Dojo de clan).", "Research resource (clan Dojo)."),
    "Mutagen Sample": ("Ressource de recherche (Dojo de clan).", "Research resource (clan Dojo)."),
}

# ressources a part, rattachees a une planete (wiki officiel et guides)
EN_PLUS = {
    "Earth": [("Cetus Wisp", "Plaines d'Eidolon et primes de Cetus", "Plains of Eidolon and Cetus bounties"),
              ("Eidolon Shard", "En capturant les Eidolons, la nuit, dans les Plaines d'Eidolon",
               "By capturing Eidolons at night in the Plains of Eidolon")],
    "Lua": [("Oxium", "Apollo (Lua) : sur les Ospreys Oxium Corpus", "Apollo (Lua): from Corpus Oxium Ospreys")],
    "Jupiter": [("Oxium", "Io ou Elara (Jupiter) : sur les Ospreys Oxium Corpus", "Io or Elara (Jupiter): from Corpus Oxium Ospreys")],
    "Neptune": [("Oxium", "Galatea ou Despina (Neptune) : sur les Ospreys Oxium Corpus",
                 "Galatea or Despina (Neptune): from Corpus Oxium Ospreys")],
    "Uranus": [("Tellurium", "Ophelia (Uranus), les missions Archwing et la Forteresse Kuva",
                "Ophelia (Uranus), Archwing missions and the Kuva Fortress")],
    "Kuva Fortress": [("Kuva", "Siphons et Inondations Kuva (missions marquées sur la carte), Taveuni (Forteresse Kuva)",
                       "Kuva Siphons and Floods (marked on the star chart), Taveuni (Kuva Fortress)")],
}
PARTOUT = [("Cryotic", "N'importe quelle mission Excavation", "Any Excavation mission"),
           ("Nitain Extract", "Boutique Nightwave (contre des Crédits Nightwave)", "Nightwave shop (for Nightwave Credits)")]

# --- mondes ouverts (WFCD) : minerais et gemmes (laser rouge / bleu), alliages et pierres taillees, plantes, poissons
MONDES = {"Eidolon": "Earth", "Solaris": "Venus", "Deimos": "Deimos"}
NOM_MONDE = {"Earth": ("Plaines d'Eidolon", "Plains of Eidolon"), "Venus": ("Vallée Orbis", "Orb Vallis"),
             "Deimos": ("Faille Cambion", "Cambion Drift")}
PECHEUR = {"Earth": ("Fisher Hai-Luk (Cetus)", "Fisher Hai-Luk (Cetus)"), "Venus": ("The Business (Fortuna)", "The Business (Fortuna)"),
           "Deimos": ("Daughter (Nécralisk)", "Daughter (Necralisk)")}
monde = {p: {"minerais": [], "gemmes": [], "fabriques": [], "plantes": [], "poissons": []} for p in MONDES.values()}
plantes_planete = {}

for i in load(WFCD / "Resources.json"):
    ch, desc = i.get("uniqueName", ""), (i.get("description") or "").replace("\n", " ")
    f_, e_ = noms(ch, i["name"])
    m = re.search(r"/Gems/(Eidolon|Solaris|Deimos)/", ch)
    if i.get("type") == "Gem" and m:
        p = MONDES[m.group(1)]
        vend = re.search(r"Blueprint sold by ([^.]+?) in", desc)
        if vend:
            monde[p]["fabriques"].append({"id": ch, "fr": f_, "en": e_, "s": [f"Fonderie (schéma chez {vend.group(1)})", f"Foundry (blueprint from {vend.group(1)})"]})
        else:
            c = re.search(r"from (Red|Blue|Yellow) Mining", desc)
            cf, ce = {"Red": ("rouge", "red"), "Blue": ("bleu", "blue"), "Yellow": ("jaune", "yellow")}.get(c.group(1) if c else "", ("", ""))
            sf, se = (f"Minage (filon {cf})", f"Mining ({ce} vein)") if cf else ("Minage", "Mining")
            if "can only be mined by the Advanced" in desc:
                sf, se = sf + " · outil de minage avancé", se + " · advanced mining tool"
            monde[p]["minerais" if "Ore" in ch.rsplit("/", 1)[-1] else "gemmes"].append({"id": ch, "fr": f_, "en": e_, "s": [sf, se]})
    elif i.get("type") == "Plant":
        m = re.search(r"(?:grow|found commonly) on ([A-Za-z ,]+)$", desc.strip())
        if not m:
            continue
        lieux = [x.strip() for x in re.split(r",| and ", m.group(1)) if x.strip()]
        jour = "jour" if "during the day" in desc else "nuit" if "at night" in desc else ""
        e = {"id": ch, "fr": f_, "en": e_, "s": [f"Cueillette{' (le ' + jour + ')' if jour else ''}",
                                                 f"Gathering{' (' + ('day' if jour == 'jour' else 'night') + ')' if jour else ''}"]}
        for lieu in lieux:
            if lieu == "Earth" and jour:
                monde["Earth"]["plantes"].append(e)
            else:
                plantes_planete.setdefault(lieu, []).append(e)

vus = set()
for i in load(WFCD / "Fish.json"):
    m = re.search(r"/Fish/(Eidolon|Solaris|Deimos)/", i["uniqueName"])
    if not m or i["name"] in vus:
        continue
    vus.add(i["name"])
    p = MONDES[m.group(1)]
    base = re.sub(r"(Large|Medium|Small)$", "", i["uniqueName"])
    f_, e_ = noms(base, i["name"])
    if f_ == i["name"]:
        f_ = noms(base + "Large", i["name"])[0]
    f_ = f_.title() if f_.isupper() else f_
    monde[p]["poissons"].append({"id": base, "fr": f_, "en": e_, "s": [f"Pêche · à revendre ou découper chez {PECHEUR[p][0]}",
                                                                          f"Fishing · sell or cut up at {PECHEUR[p][1]}"]})


def entree(nom, s=None):
    ch = par_nom.get(nom)
    if not ch:
        raise SystemExit(f"ressource introuvable : {nom}")
    f_, e_ = noms(ch, nom)
    d = {"id": ch, "fr": f_, "en": e_}
    if nom in RARETE:
        d["r"] = RARETE[nom]
    if s:
        d["s"] = list(s)
    elif nom in MEILLEUR:
        d["s"] = list(MEILLEUR[nom])
    if nom in NOTES:
        d["note"] = list(NOTES[nom])
    return d


sortie = []
for p, liste in PLANETES:
    pf, pe = nom_planete(p)
    d = {"k": p.lower().replace(" ", "-"), "fr": pf, "en": pe,
         "region": [entree(n) for n in liste],
         "plus": [entree(n, (f, e)) for n, f, e in EN_PLUS.get(p, [])],
         "plantes": plantes_planete.get(p, [])}
    if p in NOM_MONDE:
        d["monde"] = {"fr": NOM_MONDE[p][0], "en": NOM_MONDE[p][1],
                      **{k: sorted(v, key=lambda x: x["fr"]) for k, v in monde[p].items()}}
    sortie.append(d)

def icone(ch):
    for c in (ch, ch + "Large", ch + "Medium", ch + "Small"):
        if (res.get(c) or {}).get("icon"):
            return res[c]["icon"]
    return ""


def avec_icones(liste):
    for x in liste:
        x["i"] = icone(x["id"])


for p in sortie:
    for k in ("region", "plus", "plantes"):
        avec_icones(p[k])
    for k, v in (p.get("monde") or {}).items():
        if isinstance(v, list):
            avec_icones(v)
data = {"planetes": sortie, "partout": [entree(n, (f, e)) for n, f, e in PARTOUT]}
avec_icones(data["partout"])
OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(sortie)} planètes -> {OUT} ({OUT.stat().st_size // 1024} Ko)")
for p in sortie:
    m = p.get("monde")
    print(p["fr"], len(p["region"]), "+", len(p["plus"]), "plantes", len(p["plantes"]),
          ("| " + m["fr"] + " " + str({k: len(v) for k, v in m.items() if isinstance(v, list)})) if m else "")
