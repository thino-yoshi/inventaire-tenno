# Lotus Desk

Outil Windows pour Warframe : lit ton inventaire (en lecture seule) et l'estime avec les prix de
[Warframe Market](https://warframe.market). Une alternative légère à AlecaFrame.

- Pièces Prime, sets, arcanes, mods (au bon rang) et reliques, avec leur prix minimum en platine
- Ducats et pièces à garder pour Baro Ki'Teer, avec sa prochaine visite
- Connexion à Warframe Market : vente en un clic, vente en lot, alertes quand on te dépasse,
  statut « en jeu » automatique
- Liste de souhaits avec alertes de prix, valeur moyenne des reliques
- 15 langues pour les noms d'items, interface en français et en anglais
- Mise à jour automatique au démarrage depuis les releases de ce dépôt

## Installation

Télécharger `LotusDesk.exe` dans la dernière
[release](https://github.com/thino-yoshi/inventaire-tenno/releases/latest) et le lancer
(pas besoin de Python). Il se met ensuite à jour tout seul.

L'app s'ouvre dans sa propre fenêtre. Fermer la fenêtre la range près de l'horloge (icône Lotus) ;
clic droit sur l'icône puis « Quitter » pour l'arrêter.

## Publier une nouvelle version

```powershell
.\tools\release.ps1 1.0.1 "Ce qui change"
```

Le script met le numéro de version dans `app.py`, construit `LotusDesk.exe`, crée le commit, le
tag et la release GitHub. Les apps déjà installées se mettent à jour au démarrage suivant.

## Avertissement

L'app lit la mémoire du jeu en lecture seule, comme Overwolf/AlecaFrame. Outil non officiel :
utilisation à tes risques. La connexion à Warframe Market n'est pas officielle non plus.
Données d'items : [warframe-public-export-plus](https://github.com/calamity-inc/warframe-public-export-plus).
Icônes : Digital Extremes.
