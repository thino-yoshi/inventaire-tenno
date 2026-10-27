// Lotus Desk : boite aux lettres des signalements et des erreurs automatiques.
// Cloudflare Worker + base D1 (liaison "DB"). Secret MODO_CLES = "Mavis:cle1,Thigrou:cle2".
// Public : POST /signalement, POST /erreur (limites par adresse IP).
// Modos (en-tete X-Modo-Cle) : GET /modo/moi, /modo/nb, /modo/liste?type=..., POST /modo/maj.

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS signalements (id INTEGER PRIMARY KEY AUTOINCREMENT, cree INTEGER, version TEXT,
     langue TEXT, os TEXT, message TEXT, journaux TEXT, statut TEXT DEFAULT 'nouveau', pris_par TEXT, note TEXT)`,
  `CREATE TABLE IF NOT EXISTS erreurs (id INTEGER PRIMARY KEY AUTOINCREMENT, cle TEXT UNIQUE, code TEXT, detail TEXT,
     fois INTEGER DEFAULT 0, premiere INTEGER, derniere INTEGER, versions TEXT DEFAULT '', extrait TEXT,
     statut TEXT DEFAULT 'nouveau', pris_par TEXT, note TEXT)`,
  `CREATE TABLE IF NOT EXISTS erreur_install (erreur_id INTEGER, install TEXT, PRIMARY KEY (erreur_id, install))`,
  `CREATE TABLE IF NOT EXISTS limites (cle TEXT PRIMARY KEY, n INTEGER, fenetre INTEGER)`,
];
let pret = false;
const STATUTS = ["nouveau", "en_cours", "regle"];
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { "Content-Type": "application/json; charset=utf-8" } });
const coupe = (s, n) => String(s ?? "").slice(0, n);
const maintenant = () => Math.floor(Date.now() / 1000);

async function schema(db) {
  if (pret) return;
  await db.batch(SCHEMA.map(q => db.prepare(q)));
  pret = true;
}

async function sha(texte) {
  const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(texte));
  return [...new Uint8Array(h)].slice(0, 12).map(b => b.toString(16).padStart(2, "0")).join("");
}

// au plus `max` envois par heure et par adresse IP (l'adresse n'est gardee que hachee)
async function limite(env, req, quoi, max) {
  const ip = req.headers.get("CF-Connecting-IP") || "?";
  const cle = quoi + ":" + await sha(ip + (env.MODO_CLES || ""));
  const heure = Math.floor(maintenant() / 3600);
  const row = await env.DB.prepare("SELECT n, fenetre FROM limites WHERE cle = ?").bind(cle).first();
  const n = row && row.fenetre === heure ? row.n : 0;
  if (n >= max) return false;
  await env.DB.prepare("INSERT INTO limites (cle, n, fenetre) VALUES (?, ?, ?) ON CONFLICT(cle) DO UPDATE SET n = excluded.n, fenetre = excluded.fenetre")
    .bind(cle, n + 1, heure).run();
  return true;
}

// cle modo -> prenom (null si la cle est fausse)
function modo(env, req) {
  const donnee = req.headers.get("X-Modo-Cle") || "";
  if (donnee.length < 8) return null;
  for (const paire of String(env.MODO_CLES || "").split(",")) {
    const i = paire.indexOf(":");
    if (i > 0 && paire.slice(i + 1).trim() === donnee) return paire.slice(0, i).trim();
  }
  return null;
}

async function lireJson(req, max) {
  const texte = await req.text();
  if (texte.length > max) throw new Error("trop_gros");
  return JSON.parse(texte || "{}");
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      if (!env.DB) return json({ erreur: "base_absente" }, 500);
      await schema(env.DB);

      if (p === "/" ) return json({ ok: true, service: "Lotus Desk" });

      // ------------------------------------------------ public
      if (p === "/signalement" && req.method === "POST") {
        const b = await lireJson(req, 120000);
        const message = coupe(b.message, 5000).trim();
        if (!message) return json({ erreur: "message_vide" }, 400);
        if (!await limite(env, req, "sig", 10)) return json({ erreur: "trop_de_messages" }, 429);
        await env.DB.prepare("INSERT INTO signalements (cree, version, langue, os, message, journaux) VALUES (?, ?, ?, ?, ?, ?)")
          .bind(maintenant(), coupe(b.version, 20), coupe(b.langue, 10), coupe(b.os, 60), message, coupe(b.journaux, 100000)).run();
        return json({ ok: true });
      }
      if (p === "/erreur" && req.method === "POST") {
        const b = await lireJson(req, 20000);
        const code = coupe(b.code, 60).trim(), detail = coupe(b.detail, 300).trim();
        if (!code) return json({ erreur: "code_vide" }, 400);
        if (!await limite(env, req, "err", 60)) return json({ erreur: "trop_d_erreurs" }, 429);
        const cle = await sha(code + "|" + detail);
        const t = maintenant(), version = coupe(b.version, 20);
        await env.DB.prepare(`INSERT INTO erreurs (cle, code, detail, fois, premiere, derniere, versions, extrait)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(cle) DO UPDATE SET fois = fois + 1, derniere = excluded.derniere, extrait = excluded.extrait,
              versions = CASE WHEN instr(',' || versions || ',', ',' || excluded.versions || ',') > 0 THEN versions
                              ELSE versions || ',' || excluded.versions END,
              statut = CASE WHEN statut = 'regle' THEN 'nouveau' ELSE statut END`)
          .bind(cle, code, detail, t, t, version, coupe(b.extrait, 4000)).run();
        const e = await env.DB.prepare("SELECT id FROM erreurs WHERE cle = ?").bind(cle).first();
        if (b.install) await env.DB.prepare("INSERT OR IGNORE INTO erreur_install (erreur_id, install) VALUES (?, ?)")
          .bind(e.id, await sha(coupe(b.install, 80))).run();
        return json({ ok: true });
      }

      // ------------------------------------------------ modos
      if (p.startsWith("/modo/")) {
        const qui = modo(env, req);
        if (!qui) return json({ erreur: "cle_refusee" }, 403);
        if (p === "/modo/moi") return json({ ok: true, qui });
        if (p === "/modo/nb") {
          const s = await env.DB.prepare("SELECT COUNT(*) AS n FROM signalements WHERE statut = 'nouveau'").first();
          const e = await env.DB.prepare("SELECT COUNT(*) AS n FROM erreurs WHERE statut = 'nouveau'").first();
          const d = await env.DB.prepare("SELECT MAX(m) AS m FROM (SELECT MAX(cree) AS m FROM signalements UNION ALL SELECT MAX(derniere) FROM erreurs)").first();
          return json({ signalements: s.n, erreurs: e.n, dernier: d.m || 0 });
        }
        if (p === "/modo/liste") {
          const type = url.searchParams.get("type") === "erreurs" ? "erreurs" : "signalements";
          if (type === "signalements") {
            const r = await env.DB.prepare("SELECT * FROM signalements ORDER BY id DESC LIMIT 300").all();
            return json({ liste: r.results });
          }
          const r = await env.DB.prepare(`SELECT e.*, (SELECT COUNT(*) FROM erreur_install i WHERE i.erreur_id = e.id) AS utilisateurs
                                          FROM erreurs e ORDER BY e.derniere DESC LIMIT 300`).all();
          return json({ liste: r.results });
        }
        if (p === "/modo/maj" && req.method === "POST") {
          const b = await lireJson(req, 10000);
          const table = b.type === "erreurs" ? "erreurs" : "signalements";
          const id = Number(b.id);
          if (!id) return json({ erreur: "id_absent" }, 400);
          if (b.supprimer) {
            await env.DB.prepare(`DELETE FROM ${table} WHERE id = ?`).bind(id).run();
            if (table === "erreurs") await env.DB.prepare("DELETE FROM erreur_install WHERE erreur_id = ?").bind(id).run();
            return json({ ok: true });
          }
          const statut = STATUTS.includes(b.statut) ? b.statut : null;
          await env.DB.prepare(`UPDATE ${table} SET statut = COALESCE(?, statut), note = COALESCE(?, note),
                                pris_par = CASE WHEN ? = 'en_cours' THEN ? ELSE pris_par END WHERE id = ?`)
            .bind(statut, b.note === undefined ? null : coupe(b.note, 500), statut, qui, id).run();
          return json({ ok: true });
        }
      }
      return json({ erreur: "introuvable" }, 404);
    } catch (e) {
      return json({ erreur: e.message === "trop_gros" ? "trop_gros" : "erreur_serveur" }, e.message === "trop_gros" ? 413 : 500);
    }
  },
};
