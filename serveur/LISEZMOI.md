# Boîte aux lettres Lotus Desk (Cloudflare Worker)

Reçoit les signalements des utilisateurs et les erreurs automatiques anonymes ; l'onglet **Modo**
de l'appli les lit avec une clé modo.

Installation (tableau de bord Cloudflare) :
1. Workers et Pages → créer le Worker `lotusdesk-modo` → Modifier le code → coller `worker.js` → Déployer.
2. Stockage et bases de données → D1 → créer la base `lotusdesk`.
3. Worker → Paramètres → Liaisons → Ajouter → Base de données D1 → nom de variable `DB` → base `lotusdesk`.
4. Worker → Paramètres → Variables et secrets → Ajouter → type **Secret** → nom `MODO_CLES`,
   valeur `Mavis:cle1,Thigrou:cle2` (au moins 8 caractères par clé, jamais dans le code).
5. Mettre l'adresse du Worker dans `MODO_URL` (app.py).

Les tables sont créées toutes seules au premier appel.
