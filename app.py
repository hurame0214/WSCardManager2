import os, re, sqlite3, urllib.parse
from datetime import date
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, render_template_string
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
    """Official public card detail URL."""
    return OFFICIAL + "/cardlist/?" + urllib.parse.urlencode({"cardno": card_number})


def parse_official_json(payload, card_number):
    """Select only an exact card-number match from the official JSON response."""
    number = (card_number or "").strip().upper()
    if not number:
        raise ValueError("カード番号を入力してください。")
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        raise ValueError("公式サイトの応答形式が変更された可能性があります。")
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("card_number", "")).strip().upper() != number:
            continue
        picture = str(item.get("picture") or "").strip().lstrip("/")
        # The API supplies a relative card-image filename, not a full URL.
        image_url = urllib.parse.urljoin(OFFICIAL + "/wordpress/wp-content/images/cardlist/", picture) if picture else ""
        return {
            "url": official_card_url(number),
            "name": str(item.get("card_name") or "").strip(),
            "card_number": number,
            "rarity": str(item.get("rare") or "").strip(),
            "image_url": image_url,
            "title": "",
        }
    raise ValueError("公式サイトで一致するカード番号が見つかりませんでした。")


def fetch_official(card_number=None, name=None):
    number = (card_number or "").strip().upper()
    if not number:
        raise ValueError("カード番号を入力してください。")
    url = OFFICIAL + "/manage/CardListUser/searchJson"
    response = requests.get(
        url,
        params={"keyword": number, "keyword_type[]": "no"},
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                 "Referer": OFFICIAL + "/cardlist/search/"},
        timeout=20,
    )
    response.raise_for_status()
    return parse_official_json(response.json(), number)


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


BULK_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>カード一括登録</title><link rel="stylesheet" href="/static/style.css"><style>body{background:#f4f5f8}main{max-width:900px;margin:35px auto;padding:0 16px}.bulk-box{background:white;border:1px solid #ddd;border-radius:12px;padding:25px}textarea{box-sizing:border-box;width:100%;min-height:240px;padding:12px;font-size:16px}button{cursor:pointer;padding:12px 25px;background:#5956df;color:white;border:0;border-radius:6px}a{color:#3939b3}.results{margin-top:22px}li{margin:7px 0}.error{color:#bd2536}.success{color:#176d43}.purchase-fields{display:flex;flex-wrap:wrap;gap:14px;margin:20px 0}.purchase-fields label{display:flex;flex-direction:column;gap:6px;flex:1;min-width:180px}.purchase-fields input{padding:10px;font-size:16px;border:1px solid #bbb;border-radius:6px}</style></head><body><header class="top"><a class="brand" href="/">WS Card Manager</a></header><main><div class="bulk-box"><h1>カード一括登録</h1><p>カード番号を1行に1件ずつ入力してください。公式サイトからカード名・レアリティ・画像URLを取得します。</p><p>すでに同じカード番号が登録されている場合はスキップします。登録済みカードの購入・売却履歴は変更しません。</p><form method="post"><textarea name="numbers" required placeholder="DAL/W79-001&#10;THP/S130-001">{{ numbers }}</textarea><div class="purchase-fields"><label>購入枚数<input name="buy_quantity" type="number" min="0" step="1" value="{{ buy_quantity if buy_quantity is defined else 1 }}" required></label><label>購入合計金額（円）<input name="buy_total_amount" type="number" min="0" step="1" value="{{ buy_total_amount if buy_total_amount is defined else '' }}" placeholder="未入力の場合は0円"></label><label>購入日<input name="trans_date" id="bulk_purchase_date" type="date" value="{{ trans_date if trans_date is defined else '' }}"></label></div><p>購入情報は新しく登録する各カードに共通で適用します。</p><p><button type="submit">まとめて登録する</button>　<a href="/">カード一覧に戻る</a></p></form>{% if results is not none %}<div class="results"><h2>処理結果</h2><p>登録：{{ created }}件 / スキップ：{{ skipped }}件 / エラー：{{ failed }}件</p><ul>{% for r in results %}<li class="{{ 'error' if r.status == 'エラー' else 'success' }}">{{ r.number }}：{{ r.status }}{% if r.detail %}（{{ r.detail }}）{% endif %}</li>{% endfor %}</ul></div>{% endif %}</div></main><script>const dateInput=document.getElementById('bulk_purchase_date');if(dateInput&&!dateInput.value){const now=new Date();dateInput.value=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10);}</script></body></html>"""

@app.route("/bulk", methods=["GET", "POST"])
def bulk_register():
    if request.method == "GET":
        return render_template_string(BULK_HTML, numbers="", results=None)
    try:
        buy_quantity = int(request.form.get("buy_quantity") or 0)
        buy_total = int(request.form.get("buy_total_amount") or 0)
        buy_date = request.form.get("trans_date") or date.today().isoformat()

        if buy_quantity < 0 or buy_total < 0:
            raise ValueError("購入枚数と購入金額は0以上にしてください")
        if buy_quantity == 0 and buy_total > 0:
            raise ValueError("購入金額を入力した場合は購入枚数も入力してください")
        date.fromisoformat(buy_date)
    except (ValueError, TypeError) as e:
        return str(e), 400
    raw = request.form.get("numbers", "")
    numbers = [x.strip().upper() for x in raw.splitlines() if x.strip()]
    if len(numbers) > 100:
        return render_template_string(BULK_HTML, numbers=raw, results=[{"number":"-", "status":"エラー", "detail":"一度に登録できるのは100件までです"}], created=0, skipped=0, failed=1), 400
    results = []
    created = skipped = failed = 0
    seen = set()
    for number in numbers:
        if number in seen:
            skipped += 1
            results.append({"number":number, "status":"スキップ", "detail":"入力内で重複"})
            continue
        seen.add(number)
        try:
            with db() as c:
                existing = c.execute("SELECT id FROM cards WHERE UPPER(card_number)=?", (number,)).fetchone()
                if existing:
                    skipped += 1
                    results.append({"number":number, "status":"スキップ", "detail":"登録済み"})
                    continue
            data = fetch_official(number)
            if not data.get("name"):
                raise ValueError("公式カード名が取得できませんでした")
            with db() as c:
                cursor = c.execute(
                    "INSERT INTO cards(name,card_number,title,rarity,image_url,notes) VALUES(?,?,?,?,?,?)",
                    (data["name"], number, data.get("title", ""), data.get("rarity", ""), data.get("image_url", ""), "")
                )
                card_id = cursor.lastrowid

            if buy_quantity > 0:
                 c.execute(
                    "INSERT INTO transactions(card_id,kind,unit_price,quantity,trans_date,memo) VALUES(?,?,?,?,?,?)",
                     (
                        card_id,
                         "buy",
                         buy_total / buy_quantity,
                        buy_quantity,
                         buy_date,
                         ""
                     )
                 )

                 c.commit()
        　 created += 1
         　results.append({"number":number, "status":"登録完了", "detail":data["name"]})
        except Exception as e:
            failed += 1
            results.append({"number":number, "status":"エラー", "detail":str(e)})
    return render_template_string(BULK_HTML, numbers=raw, results=results, created=created, skipped=skipped, failed=failed)

@app.route("/images/<path:name>")
def images(name):
    return send_from_directory(IMG_DIR,name)

if __name__=="__main__":
    init_db()
    print("WS Card Manager: http://127.0.0.1:5000")
    app.run(host="127.0.0.1",port=5000,debug=False)
