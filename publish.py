#!/usr/bin/env python3
"""Publish the local catalog to the live GitHub Pages site.

Reads products.db (what the admin UI edits), rewrites the fallback
catalog inside index.html, then git-commits and pushes.
GitHub Pages rebuilds about a minute later.
"""
import json, os, re, sqlite3, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "products.db")
INDEX = os.path.join(ROOT, "index.html")
LIVE = "https://evermore13tay.github.io/shearcraft/"


def main():
    if not os.path.exists(DB):
        sys.exit("products.db not found — start the server once (start.command) and add products first.")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM products ORDER BY position, rowid").fetchall()
    con.close()
    products = [{"id": r["id"], "name": r["name"], "price": r["price"], "upc": r["upc"] or "",
                 "description": r["description"], "images": json.loads(r["images"]),
                 "colors": json.loads(r["colors"])} for r in rows]
    if not products:
        sys.exit("Catalog is empty — add a product in the admin first.")

    js = json.dumps(products, indent=2, ensure_ascii=False).replace("</", "<\\/")
    src = open(INDEX, encoding="utf-8").read()
    new, n = re.subn(r"const PRODUCTS_SEED = \[.*?\n\];",
                     "const PRODUCTS_SEED = " + js + ";", src, count=1, flags=re.S)
    if n != 1:
        sys.exit("Could not find the PRODUCTS_SEED block in index.html — was it edited?")
    open(INDEX, "w", encoding="utf-8").write(new)

    def git(*args):
        subprocess.run(["git", "-C", ROOT] + list(args), check=True)

    git("add", "-A")
    if subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode == 0:
        print("No changes to publish.")
        return
    git("-c", "user.name=Evermore13Tay", "-c", "user.email=evermore13tay@users.noreply.github.com",
        "commit", "-m", f"Publish catalog ({len(products)} products)")
    git("push")
    print(f"Published {len(products)} products. Live in ~1 min: {LIVE}")


if __name__ == "__main__":
    main()
