import os, re, sqlite3, urllib.parse
from datetime import date
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory
import requests
from bs4 import BeautifulSoup

APP_DIR = Path.home() / "WSCardManager"
APP_DIR.mkdir(exist_ok=True)
DB_PATH = APP_DIR / "cards.db"
IMG_DIR = APP_DIR / "images"
IMG_DIR.mkdir(exist_ok=True)
OFFICIAL = "https://ws-tcg.com"

app = Flask(__name__)

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      card_number TEXT DEFAULT '',
      title TEXT DEFAULT '',
      rarity TEXT DEFAULT '',
      image_path TEXT DEFAULT '',
      image_url TEXT DEFAULT '',
      notes TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS transactions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('buy','sell')),
      unit_price REAL NOT NULL,
      quantity INTEGER NOT NULL CHECK(quantity > 0),
      trans_date TEXT NOT NULL,
      memo TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
    CREATE INDEX IF NOT EXISTS idx_tx_card ON transactions(card_id);
    """)
    c.commit(); c.close()

def calc(card_id):
    c=db()
    rows=c.execute("SELECT * FROM transactions WHERE card_id=? ORDER BY trans_date,id",(card_id,)).fetchall()
    c.close()
    qty=cost=buy_total=sell_total=realized=0.0
    for r in rows:
        q=r["quantity"]; p=r["unit_price"]
        if r["kind"]=="buy":
            qty += q; cost += q*p; buy_total += q*p
        elif q <= qty:
            avg = cost/qty if qty else 0
            realized += q*p - avg*q
            qty -= q; cost -= avg*q; sell_total += q*p
    return {"qty":int(qty),"cost":cost,"avg":cost/qty if qty else 0,"buy_total":buy_total,"sell_total":sell_total,"realized":realized}

def card_row(r):
    z=calc(r["id"])
    return dict(id=r["id"],name=r["name"],card_number=r["card_number"],title=r["title"],
                rarity=r["rarity"],image_url=r["image_url"],image_path=r["image_path"],
                notes=r["notes"],**z)

def official_card_url(card_number):
    return OFFICIAL + "/cardlist/?cardno=" + urllib.parse.quote(card_number,safe="")

def fetch_official(card_number=None, name=None):
    """Read only a card entry matching the requested number; never use site logos."""
    number = (card_number or "").strip().upper()
    if not number:
        raise ValueError("カード番号を入力してください。")
    url = official_card_url(number)
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    # The official site may render card details with JavaScript. In that case
    # requests cannot read them, so return an error rather than false data.
    pattern = re.compile(r"^(.+?)\s*\(" + re.escape(number) + r"\)\s*-\s*([A-Z0-9+]+)$", re.I)
    card_name = ""
    rarity = ""
    for element in soup.find_all(["a", "h1", "h2", "h3", "h4", "p", "span", "div"]):
        value = element.get_text(" ", strip=True)
        if len(value) > 200:
            continue
        match = pattern.match(value)
        if match:
            card_name, rarity = match.group(1).strip(), match.group(2).strip()
            break
    if not card_name:
        # Some official pages display the number and name in separate nodes.
        # A matching image alt is stronger evidence than the generic page title.
        for img in soup.find_all("img"):
            alt = (img.get("alt") or "").strip()
            if alt and alt not in ("カードリスト", "Card List"):
                parent = img.find_parent(["article", "li", "section", "div"])
                if parent and number in parent.get_text(" ", strip=True).upper():
                    card_name = alt
                    break
    if not card_name:
        raise ValueError("公式ページからカード情報を確認できませんでした。手動入力してください。")
    image_url = ""
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if alt != card_name:
            continue
        src = img.get("data-src") or img.get("src") or ""
        if src and not src.startswith("data:"):
            image_url = urllib.parse.urljoin(response.url, src)
            break
    title = ""
    # Extract a series name only when a labeled field is present.
    for element in soup.find_all(["dt", "th"]):
        if element.get_text(" ", strip=True) in ("ネオスタンダード区分", "収録商品"):
            sibling = element.find_next_sibling(["dd", "td"])
            if sibling:
                title = sibling.get_text(" ", strip=True)
                if title and title != "-":
                    break
    return {"url": response.url, "name": card_name, "card_number": number,
            "rarity": rarity, "image_url": image_url, "title": title}

def download_image(url, card_id):
    if not url: return ""
    try:
        r=requests.get(url,headers={"User-Agent":"Mozilla/5.0 WSCardManager/2.0"},timeout=20)
        r.raise_for_status()
        ct=r.headers.get("content-type","").lower()
        ext=".jpg" if "jpeg" in ct or "jpg" in ct else ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
        p=IMG_DIR/f"card_{card_id}{ext}"
        p.write_bytes(r.content)
        return p.name
    except Exception:
        return ""

@app.route("/")
def index():
    q=request.args.get("q","").strip()
    c=db()
    if q:
        rows=c.execute("SELECT * FROM cards WHERE name LIKE ? OR card_number LIKE ? OR title LIKE ? ORDER BY name",
                       (f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
    else:
        rows=c.execute("SELECT * FROM cards ORDER BY name").fetchall()
    c.close()
    cards=[card_row(r) for r in rows]
    stats={"cards":len(cards),"qty":sum(x["qty"] for x in cards),
           "buy":sum(x["buy_total"] for x in cards),"sell":sum(x["sell_total"] for x in cards),
           "profit":sum(x["realized"] for x in cards),"cost":sum(x["cost"] for x in cards)}
    return render_template("index.html",cards=cards,stats=stats,q=q)

@app.route("/card/<int:card_id>")
def card_detail(card_id):
    c=db(); r=c.execute("SELECT * FROM cards WHERE id=?",(card_id,)).fetchone()
    if not r: return "Not found",404
    tx=c.execute("SELECT * FROM transactions WHERE card_id=? ORDER BY trans_date DESC,id DESC",(card_id,)).fetchall()
    c.close()
    return render_template("card.html",card=card_row(r),transactions=[dict(x) for x in tx],today=date.today().isoformat())

@app.route("/card/new",methods=["GET","POST"])
def new_card():
    if request.method=="POST":
        f=request.form
        initial_tx=None
        qty=int(f.get("buy_quantity") or 0)
        total=int(float(f.get("buy_total_amount") or 0))
        if qty < 0 or total < 0:
            return render_template("error.html",message="買った枚数と合計金額は0以上で入力してください"),400
        if qty > 0 or total > 0:
            if qty <= 0:
                return render_template("error.html",message="初回購入を入力する場合は買った枚数を入力してください"),400
            if total <= 0:
                return render_template("error.html",message="初回購入を入力する場合は合計金額を入力してください"),400
            initial_tx = {
                "quantity": qty,
                "unit_price": total / qty,
                "trans_date": f.get("trans_date") or date.today().isoformat(),
                "memo": "",
            }
        c=db()
        cur=c.execute("INSERT INTO cards(name,card_number,title,rarity,image_url,notes) VALUES(?,?,?,?,?,?)",
                      (f["name"].strip(),f.get("card_number","").strip(),f.get("title","").strip(),
                       f.get("rarity","").strip(),f.get("image_url","").strip(),f.get("notes","").strip()))
        cid=cur.lastrowid
        if initial_tx:
            c.execute("INSERT INTO transactions(card_id,kind,unit_price,quantity,trans_date,memo) VALUES(?,?,?,?,?,?)",
                      (cid,"buy",initial_tx["unit_price"],initial_tx["quantity"],initial_tx["trans_date"],""))
        c.commit(); c.close()
        return redirect(url_for("card_detail",card_id=cid))
    return render_template("new_card.html")

def add_tx(card_id,kind,f):
    qty=int(f["quantity"])
    if qty<=0: raise ValueError("invalid transaction")
    price=float(f["unit_price"])
    if price<0: raise ValueError("invalid transaction")
    c=db()
    if kind=="sell":
        z=calc(card_id)
        if qty>z["qty"]: raise ValueError("売却枚数が所持枚数を超えています")
    c.execute("INSERT INTO transactions(card_id,kind,unit_price,quantity,trans_date,memo) VALUES(?,?,?,?,?,?)",
              (card_id,kind,price,qty,f.get("trans_date") or date.today().isoformat(),f.get("memo","").strip()))
    c.commit(); c.close()

@app.post("/card/<int:card_id>/tx")
def transaction(card_id):
    try:
        add_tx(card_id,request.form["kind"],request.form)
    except Exception as e:
        return render_template("error.html",message=str(e)),400
    return redirect(url_for("card_detail",card_id=card_id))

@app.post("/card/<int:card_id>/delete")
def delete_card(card_id):
    c=db(); c.execute("DELETE FROM cards WHERE id=?",(card_id,)); c.commit(); c.close()
    return redirect(url_for("index"))

@app.post("/card/<int:card_id>/edit")
def edit_card(card_id):
    f=request.form; c=db()
    c.execute("UPDATE cards SET name=?,card_number=?,title=?,rarity=?,image_url=?,notes=? WHERE id=?",
              (f["name"].strip(),f.get("card_number","").strip(),f.get("title","").strip(),
               f.get("rarity","").strip(),f.get("image_url","").strip(),f.get("notes","").strip(),card_id))
    c.commit(); c.close(); return redirect(url_for("card_detail",card_id=card_id))

@app.get("/api/official")
def api_official():
    try:
        result=fetch_official(request.args.get("card_number"),request.args.get("name"))
        return jsonify({"ok":True,"data":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.post("/card/<int:card_id>/official-image")
def official_image(card_id):
    c=db(); r=c.execute("SELECT * FROM cards WHERE id=?",(card_id,)).fetchone()
    if not r: return "Not found",404
    try:
        data=fetch_official(r["card_number"],r["name"])
        image_path=download_image(data.get("image_url",""),card_id)
        c.execute("UPDATE cards SET name=?,card_number=?,title=?,rarity=?,image_url=?,image_path=? WHERE id=?",
                  (data.get("name") or r["name"],data.get("card_number") or r["card_number"],
                   data.get("title") or r["title"],data.get("rarity") or r["rarity"],
                   data.get("image_url") or r["image_url"],image_path or r["image_path"],card_id))
        c.commit()
    except Exception:
        pass
    c.close(); return redirect(url_for("card_detail",card_id=card_id))

@app.route("/images/<path:name>")
def images(name):
    return send_from_directory(IMG_DIR,name)

if __name__=="__main__":
    init_db()
    print("WS Card Manager: http://127.0.0.1:5000")
    app.run(host="127.0.0.1",port=5000,debug=False)
