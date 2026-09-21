#!/usr/bin/env python3
"""ShearCraft store server — static site + JSON API + SQLite. Zero dependencies.

Run:  ADMIN_PASSWORD=your-secret python3 server.py [port]
Then: storefront at http://127.0.0.1:8000/  admin at http://127.0.0.1:8000/admin.html
"""
import base64, binascii, json, os, re, sqlite3, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "products.db")
IMG_DIR = os.path.join(ROOT, "images")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")
MAX_BODY = 8_000_000  # bytes; covers base64-encoded product photos

# First-run seed so the storefront is never empty. After that, products.db is
# the source of truth and this list is ignored (delete products.db to re-seed).
SEED = [
    {"id": "pinking-shears-huitong", "name": "Hui Tong Strong & Sharp Pinking Shears — Serrated & Scalloped Fabric Scissors", "price": 17.39,
     "colors": [{"name": "Serrated 3mm", "hex": ""}, {"name": "Serrated 5mm", "hex": ""}, {"name": "Serrated 7mm", "hex": ""},
                {"name": "Scalloped 5mm", "hex": ""}, {"name": "Scalloped 7mm", "hex": ""}, {"name": "Wavy 18mm", "hex": ""}],
     "images": ["images/pinking-shears-1.jpg", "images/pinking-shears-2.jpg", "images/pinking-shears-3.jpg",
                "images/pinking-shears-4.jpg", "images/pinking-shears-5.jpg"],
     "description": "Effortlessly cuts denim, leather, and silk — clean from first tooth to last. Hand-sharpened by master craftsmen; high-carbon stainless steel resists rust and stays sharp 3x longer than standard shears. The precision serrated blade creates a smooth, finished edge that prevents fraying."},
    {"id": "pro-tailor-8", "name": "Professional 8\" Tailor Scissors", "price": 32.99,
     "colors": [{"name": "Classic Silver", "hex": "#c9c9c9"}, {"name": "Gold", "hex": "#d4af6a"}, {"name": "Matte Black", "hex": "#2b2b2b"}],
     "images": [], "description": "Forged from Japanese stainless steel with a razor-sharp convex edge. Balanced for all-day cutting on fabric, leather and denim."},
    {"id": "embroidery-4", "name": "4\" Embroidery Detail Scissors", "price": 14.99,
     "colors": [{"name": "Rose Gold", "hex": "#e0a899"}, {"name": "Silver", "hex": "#c9c9c9"}],
     "images": [], "description": "Fine pointed tips for thread work, embroidery and precision craft cuts. Includes a protective leather sheath."},
    {"id": "kitchen-shears", "name": "Heavy-Duty Kitchen Shears", "price": 21.99,
     "colors": [{"name": "Black", "hex": "#2b2b2b"}, {"name": "Red", "hex": "#b03a2e"}],
     "images": [], "description": "Come-apart blades for easy cleaning. Cuts poultry, herbs and packaging; built-in bottle opener and nutcracker."},
    {"id": "hair-cutting-6", "name": "6\" Barber Hair Cutting Shears", "price": 45.99,
     "colors": [{"name": "Silver", "hex": "#c9c9c9"}, {"name": "Rainbow", "hex": "#9b7ec4"}, {"name": "Matte Black", "hex": "#2b2b2b"}],
     "images": [], "description": "440C steel with tension adjustment screw and removable finger rest. Salon-grade sharpness for clean, quiet cuts."},
    {"id": "craft-set-3", "name": "Craft Scissors 3-Piece Set", "price": 26.99,
     "colors": [{"name": "Pastel Mix", "hex": "#d8b4c8"}, {"name": "Bold Mix", "hex": "#4a6fa5"}],
     "images": [], "description": "Three sizes (5\", 7\", 8.5\") for paper, vinyl and mixed-media craft. Titanium-coated blades resist adhesive buildup."},
]

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS products(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
        description TEXT DEFAULT '', images TEXT DEFAULT '[]',
        colors TEXT DEFAULT '[]', position INTEGER DEFAULT 0)""")
    # one-time migration: single `image` column -> `images` JSON array
    cols = [r["name"] for r in con.execute("PRAGMA table_info(products)")]
    if "images" not in cols:
        con.execute("ALTER TABLE products ADD COLUMN images TEXT DEFAULT '[]'")
        if "image" in cols:
            con.execute("UPDATE products SET images = json_array(image) WHERE image != ''")
    if "image" in cols:
        try:
            con.execute("ALTER TABLE products DROP COLUMN image")
        except sqlite3.OperationalError:
            pass  # old SQLite without DROP COLUMN: harmless to leave it
    return con


def seed_if_empty(con):
    if con.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]:
        return
    for i, p in enumerate(SEED):
        con.execute("INSERT INTO products(id, name, price, description, images, colors, position) VALUES(?,?,?,?,?,?,?)",
                    (p["id"], p["name"], p["price"], p["description"], json.dumps(p["images"]), json.dumps(p["colors"]), i))
    con.commit()


def row_to_product(r):
    return {"id": r["id"], "name": r["name"], "price": r["price"],
            "description": r["description"], "images": json.loads(r["images"]),
            "colors": json.loads(r["colors"])}


def validate(p):
    """Trust-boundary check for admin writes. Returns error string or None."""
    if not ID_RE.match(str(p.get("id", ""))):
        return "id must be lowercase letters/numbers/dashes"
    if not str(p.get("name", "")).strip():
        return "name required"
    try:
        price = float(p.get("price"))
        if not 0 <= price <= 1_000_000:
            raise ValueError
    except (TypeError, ValueError):
        return "price must be a number between 0 and 1000000"
    colors = p.get("colors")
    if not isinstance(colors, list) or not colors:
        return "at least one variant/color required"
    for c in colors:
        if not isinstance(c, dict) or not str(c.get("name", "")).strip():
            return "each variant needs a name"
        if c.get("hex") and not HEX_RE.match(str(c["hex"])):
            return "color hex must be #rrggbb (or empty for a text variant)"
    images = p.get("images") or []
    if not isinstance(images, list) or any(not re.match(r"^images/[a-z0-9.-]+$", str(i)) for i in images):
        return "images must be a list of uploaded image paths"
    return None


class Handler(SimpleHTTPRequestHandler):
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return None

    def authed(self):
        return self.headers.get("X-Admin-Password") == ADMIN_PASSWORD

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/products":
            con = connect()
            rows = con.execute("SELECT * FROM products ORDER BY position, rowid").fetchall()
            con.close()
            self.send_json([row_to_product(r) for r in rows])
        elif path == "/api/check":
            code = 200 if self.authed() else 401
            self.send_json({"ok": self.authed()}, code)
        else:
            super().do_GET()  # static files

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.authed():
            return self.send_json({"error": "unauthorized"}, 401)
        body = self.read_json()
        if body is None:
            return self.send_json({"error": "bad or missing json body"}, 400)
        if path == "/api/products":
            err = validate(body)
            if err:
                return self.send_json({"error": err}, 400)
            con = connect()
            if con.execute("SELECT 1 FROM products WHERE id=?", (body["id"],)).fetchone():
                con.close()
                return self.send_json({"error": "id already exists"}, 409)
            pos = con.execute("SELECT COALESCE(MAX(position)+1, 0) AS p FROM products").fetchone()["p"]
            con.execute("INSERT INTO products(id, name, price, description, images, colors, position) VALUES(?,?,?,?,?,?,?)",
                        (body["id"], body["name"].strip(), float(body["price"]),
                         str(body.get("description", "")).strip(), json.dumps(body.get("images") or []),
                         json.dumps(body["colors"]), pos))
            con.commit()
            con.close()
            self.send_json({"ok": True})
        elif path == "/api/upload":
            name = os.path.basename(str(body.get("filename", ""))).lower()
            ext = os.path.splitext(name)[1]
            if ext not in IMG_EXT:
                return self.send_json({"error": "image must be png/jpg/webp/gif"}, 400)
            try:
                data = base64.b64decode(str(body.get("data", "")), validate=True)
            except (binascii.Error, ValueError):
                return self.send_json({"error": "bad image data"}, 400)
            if not data:
                return self.send_json({"error": "empty file"}, 400)
            os.makedirs(IMG_DIR, exist_ok=True)
            stem = re.sub(r"[^a-z0-9-]", "-", os.path.splitext(name)[0]) or "img"
            fname, i = stem + ext, 1
            while os.path.exists(os.path.join(IMG_DIR, fname)):
                fname = f"{stem}-{i}{ext}"
                i += 1
            with open(os.path.join(IMG_DIR, fname), "wb") as f:
                f.write(data)
            self.send_json({"path": f"images/{fname}"})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_PUT(self):
        m = re.match(r"^/api/products/([a-z0-9-]+)$", urlparse(self.path).path)
        if not m:
            return self.send_json({"error": "not found"}, 404)
        if not self.authed():
            return self.send_json({"error": "unauthorized"}, 401)
        body = self.read_json()
        if body is None:
            return self.send_json({"error": "bad or missing json body"}, 400)
        body["id"] = m.group(1)
        err = validate(body)
        if err:
            return self.send_json({"error": err}, 400)
        con = connect()
        cur = con.execute("UPDATE products SET name=?, price=?, description=?, images=?, colors=? WHERE id=?",
                          (body["name"].strip(), float(body["price"]), str(body.get("description", "")).strip(),
                           json.dumps(body.get("images") or []), json.dumps(body["colors"]), m.group(1)))
        n = cur.rowcount
        con.commit()
        con.close()
        if n == 0:
            return self.send_json({"error": "not found"}, 404)
        self.send_json({"ok": True})

    def do_DELETE(self):
        m = re.match(r"^/api/products/([a-z0-9-]+)$", urlparse(self.path).path)
        if not m:
            return self.send_json({"error": "not found"}, 404)
        if not self.authed():
            return self.send_json({"error": "unauthorized"}, 401)
        con = connect()
        con.execute("DELETE FROM products WHERE id=?", (m.group(1),))
        con.commit()
        con.close()
        self.send_json({"ok": True})

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    os.chdir(ROOT)
    con = connect()
    seed_if_empty(con)
    con.close()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if ADMIN_PASSWORD == "change-me":
        print("WARNING: default admin password in use — set ADMIN_PASSWORD env var", file=sys.stderr)
    print(f"Store: http://127.0.0.1:{port}/   Admin: http://127.0.0.1:{port}/admin.html")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
