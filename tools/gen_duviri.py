"""Construit assets/duviri.json pour l'onglet Info / aide -> Duviri, depuis la base WFCD :
adaptateurs Incarnon (Genesis) et ressources de Duviri, avec noms et descriptions FR / EN et image.

    python tools/gen_duviri.py CHEMIN/warframe-items/data/json
"""
import json
import sys
from pathlib import Path

WFCD = Path(sys.argv[1])
OUT = Path(__file__).resolve().parent.parent / "assets" / "duviri.json"
load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
fr = load(WFCD / "i18n" / "fr.json")
img = lambda i: ("https://cdn.warframestat.us/img/" + i["imageName"]) if i.get("imageName") else ""
CAT = {"Primary": "primaire", "Secondary": "secondaire", "Melee": "melee"}

adapt, vus = [], set()
for f in WFCD.glob("*.json"):
    try:
        data = load(f)
    except ValueError:
        continue
    for i in data if isinstance(data, list) else []:
        ch = i.get("uniqueName", "")
        if "/IncarnonAdapters/" not in ch or ch in vus:
            continue
        vus.add(ch)
        arme = i["name"].replace(" Incarnon Genesis", "")
        arme_fr = (fr.get(ch) or {}).get("name", i["name"]).replace(" Genesis Incarnon", "")
        adapt.append({"id": ch, "fr": arme_fr, "en": arme, "c": CAT.get(ch.split("/")[-2], "melee"), "i": img(i)})
adapt.sort(key=lambda a: (["primaire", "secondaire", "melee"].index(a["c"]), a["fr"]))

res = []
for i in load(WFCD / "Resources.json"):
    ch = i["uniqueName"]
    if "/Gameplay/Duviri/" not in ch:  # (DuviriMITW = laboratoires d'Albrecht, pas Duviri)
        continue
    t = fr.get(ch) or {}
    res.append({"id": ch, "fr": t.get("name") or i["name"], "en": i["name"], "i": img(i),
                "dfr": (t.get("description") or "").replace("\n\n", " ").strip(),
                "den": (i.get("description") or "").replace("\n\n", " ").strip()})
res.sort(key=lambda r: r["fr"])
OUT.write_text(json.dumps({"adapt": adapt, "res": res}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(adapt)} adaptateurs Incarnon, {len(res)} ressources -> {OUT}")
