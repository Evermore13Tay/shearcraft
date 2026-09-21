// Hui Tong store — Cloudflare Worker: static assets + JSON API + D1 (SQLite) + R2 images.
// Same API contract as server.py, so index.html / admin.html work unchanged.

const ID_RE = /^[a-z0-9][a-z0-9-]{0,60}$/;
const HEX_RE = /^#[0-9a-fA-F]{6}$/;
const IMG_EXT = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif"]);
const MAX_BODY = 8_000_000;

function json(obj, code = 200) {
  return new Response(JSON.stringify(obj), {
    status: code,
    headers: { "Content-Type": "application/json" },
  });
}

function authed(request, env) {
  return request.headers.get("X-Admin-Password") === env.ADMIN_PASSWORD;
}

function validate(p) {
  if (!ID_RE.test(String(p.id || ""))) return "id must be lowercase letters/numbers/dashes";
  if (!String(p.name || "").trim()) return "name required";
  const price = Number(p.price);
  if (!Number.isFinite(price) || price < 0 || price > 1e6) return "price must be a number between 0 and 1000000";
  if (!Array.isArray(p.colors) || !p.colors.length) return "at least one variant/color required";
  for (const c of p.colors) {
    if (!c || !String(c.name || "").trim()) return "each variant needs a name";
    if (c.hex && !HEX_RE.test(String(c.hex))) return "color hex must be #rrggbb (or empty for a text variant)";
  }
  const images = p.images || [];
  if (!Array.isArray(images) || images.some(i => !/^images\/[a-z0-9.-]+$/.test(String(i))))
    return "images must be a list of uploaded image paths";
  const upc = String(p.upc || "").trim();
  if (upc && !/^\d{8,14}$/.test(upc)) return "upc must be 8-14 digits (or empty)";
  return null;
}

async function readJson(request) {
  const n = Number(request.headers.get("Content-Length") || 0);
  if (n <= 0 || n > MAX_BODY) return null;
  try {
    return await request.json();
  } catch {
    return null;
  }
}

const rowToProduct = r => ({
  id: r.id, name: r.name, price: r.price, upc: r.upc || "", description: r.description,
  images: JSON.parse(r.images), colors: JSON.parse(r.colors),
});

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    // ---------- public API ----------
    if (path === "/api/products" && method === "GET") {
      const { results } = await env.DB.prepare("SELECT * FROM products ORDER BY position, rowid").all();
      return json(results.map(rowToProduct));
    }

    // ---------- uploaded images (KV), fall back to committed assets ----------
    if (path.startsWith("/images/") && method === "GET") {
      const { value, metadata } = await env.IMAGES.getWithMetadata(path.slice(1), { type: "arrayBuffer" });
      if (value) {
        return new Response(value, {
          headers: {
            "Content-Type": (metadata && metadata.contentType) || "image/jpeg",
            "Cache-Control": "public, max-age=31536000, immutable",
          },
        });
      }
      return env.ASSETS.fetch(request);
    }

    // ---------- admin API ----------
    if (path === "/api/check" && method === "GET") {
      return authed(request, env) ? json({ ok: true }) : json({ ok: false }, 401);
    }

    if (path === "/api/products" && method === "POST") {
      if (!authed(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await readJson(request);
      if (!body) return json({ error: "bad or missing json body" }, 400);
      const err = validate(body);
      if (err) return json({ error: err }, 400);
      const exists = await env.DB.prepare("SELECT 1 FROM products WHERE id = ?").bind(body.id).first();
      if (exists) return json({ error: "id already exists" }, 409);
      const pos = (await env.DB.prepare("SELECT COALESCE(MAX(position)+1, 0) AS p FROM products").first()).p;
      await env.DB.prepare(
        "INSERT INTO products(id, name, price, description, images, colors, position, upc) VALUES(?,?,?,?,?,?,?,?)"
      ).bind(body.id, body.name.trim(), Number(body.price), String(body.description || "").trim(),
             JSON.stringify(body.images || []), JSON.stringify(body.colors), pos,
             String(body.upc || "").trim()).run();
      return json({ ok: true });
    }

    if (path === "/api/reorder" && method === "POST") {
      if (!authed(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await readJson(request);
      if (!body || !Array.isArray(body.ids) || body.ids.some(id => !ID_RE.test(String(id))))
        return json({ error: "ids must be an array of product ids" }, 400);
      await env.DB.batch(body.ids.map((id, i) =>
        env.DB.prepare("UPDATE products SET position=? WHERE id=?").bind(i, id)));
      return json({ ok: true });
    }

    if (path === "/api/upload" && method === "POST") {
      if (!authed(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await readJson(request);
      if (!body) return json({ error: "bad or missing json body" }, 400);
      const name = String(body.filename || "").toLowerCase().split("/").pop();
      const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
      if (!IMG_EXT.has(ext)) return json({ error: "image must be png/jpg/webp/gif" }, 400);
      let bytes;
      try {
        bytes = Uint8Array.from(atob(String(body.data || "")), c => c.charCodeAt(0));
      } catch {
        return json({ error: "bad image data" }, 400);
      }
      if (!bytes.length) return json({ error: "empty file" }, 400);
      const stem = (name.slice(0, name.length - ext.length).replace(/[^a-z0-9-]/g, "-") || "img");
      let fname = stem + ext;
      for (let i = 1; await env.IMAGES.get("images/" + fname) !== null; i++) fname = `${stem}-${i}${ext}`;
      const mime = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".webp": "image/webp", ".gif": "image/gif" }[ext];
      await env.IMAGES.put("images/" + fname, bytes, { metadata: { contentType: mime } });
      return json({ path: "images/" + fname });
    }

    const m = path.match(/^\/api\/products\/([a-z0-9-]+)$/);
    if (m && method === "PUT") {
      if (!authed(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await readJson(request);
      if (!body) return json({ error: "bad or missing json body" }, 400);
      body.id = m[1];
      const err = validate(body);
      if (err) return json({ error: err }, 400);
      const res = await env.DB.prepare(
        "UPDATE products SET name=?, price=?, description=?, images=?, colors=?, upc=? WHERE id=?"
      ).bind(body.name.trim(), Number(body.price), String(body.description || "").trim(),
             JSON.stringify(body.images || []), JSON.stringify(body.colors),
             String(body.upc || "").trim(), m[1]).run();
      if (!res.meta.changes) return json({ error: "not found" }, 404);
      return json({ ok: true });
    }

    if (m && method === "DELETE") {
      if (!authed(request, env)) return json({ error: "unauthorized" }, 401);
      await env.DB.prepare("DELETE FROM products WHERE id=?").bind(m[1]).run();
      return json({ ok: true });
    }

    if (path.startsWith("/api/")) return json({ error: "not found" }, 404);

    // ---------- static site ----------
    return env.ASSETS.fetch(request);
  },
};
