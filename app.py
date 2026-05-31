from flask import Flask, render_template, request, jsonify, g
import sqlite3
import os
import re
import json
from datetime import datetime

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "senaryo.db")

# Senaryo blok tipleri (Fountain/Starc benzeri)
BLOK_TIPLERI = ("sahne", "aksiyon", "karakter", "parantez", "diyalog", "gecis", "not")


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db


@app.teardown_appcontext
def close_db(_):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS projeler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ad TEXT NOT NULL,
        aciklama TEXT,
        olusturulma TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS bolumler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proje_id INTEGER NOT NULL,
        ad TEXT NOT NULL,
        sira INTEGER DEFAULT 0,
        icerik TEXT,  -- JSON: [{tip, metin, beat?}, ...]
        guncelleme TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (proje_id) REFERENCES projeler(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS karakterler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proje_id INTEGER NOT NULL,
        ad TEXT NOT NULL,
        aciklama TEXT,
        UNIQUE(proje_id, ad),
        FOREIGN KEY (proje_id) REFERENCES projeler(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS mekanlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proje_id INTEGER NOT NULL,
        ad TEXT NOT NULL,
        aciklama TEXT,
        UNIQUE(proje_id, ad),
        FOREIGN KEY (proje_id) REFERENCES projeler(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_bolumler_proje ON bolumler(proje_id);
    """)
    conn.commit()
    conn.close()


def _ozumle_karakter_mekan(proje_id, icerik):
    """Sahne formatı: PREFIX. ZAMAN. MEKAN
        İÇ. GÜN. KAFE
        DIŞ. GECE. PARK
        İÇ./DIŞ. GÜN. ARABA
    """
    if not icerik:
        return
    try:
        bloklar = json.loads(icerik) if isinstance(icerik, str) else icerik
    except (json.JSONDecodeError, TypeError):
        return
    import re as _re

    # 3 parçayı yakala: prefix(noktayla) . zaman . mekan
    SAHNE_RE = _re.compile(
        r"^\s*(İÇ\.\s*/\s*DIŞ\.|DIŞ\.\s*/\s*İÇ\.|İÇ\.|DIŞ\.|INT\.|EXT\.|I/E\.)\s*"
        r"(GÜN/GECE|GÜN|GECE|SABAH|AKŞAM|DAY|NIGHT|DAWN|DUSK)\.\s*"
        r"(.+?)\s*$",
        _re.IGNORECASE
    )

    db = get_db()
    for blk in bloklar:
        tip = blk.get("tip")
        metin = (blk.get("metin") or "").strip()
        if not metin:
            continue
        if tip == "karakter":
            ad = _re.sub(r"\([^)]*\)", "", metin).strip().upper()
            # Sadece anlamlı karakter adlarını al: en az 2 harf, en az bir sesli
            if ad and 2 <= len(ad) <= 60 and _re.search(r"[AEIİOÖUÜ]", ad):
                db.execute("INSERT OR IGNORE INTO karakterler (proje_id, ad) VALUES (?, ?)",
                           (proje_id, ad))
        elif tip == "sahne":
            m = SAHNE_RE.match(metin)
            if not m:
                continue
            mekan = m.group(3).strip().upper().rstrip(".").strip()
            if mekan and 2 <= len(mekan) <= 80 and _re.search(r"[A-ZÇĞİÖŞÜ]", mekan):
                db.execute("INSERT OR IGNORE INTO mekanlar (proje_id, ad) VALUES (?, ?)",
                           (proje_id, mekan))
    db.commit()


@app.route("/")
def index():
    return render_template("index.html")


# --- Projeler ---

@app.route("/api/projeler", methods=["GET"])
def projeler_listele():
    db = get_db()
    rows = db.execute("""
        SELECT p.id, p.ad, p.aciklama,
               (SELECT COUNT(*) FROM bolumler b WHERE b.proje_id = p.id) AS bolum_sayisi
        FROM projeler p ORDER BY p.olusturulma DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/proje", methods=["POST"])
def proje_olustur():
    data = request.get_json(force=True)
    ad = (data.get("ad") or "").strip() or "İsimsiz Proje"
    aciklama = (data.get("aciklama") or "").strip()
    db = get_db()
    cur = db.execute("INSERT INTO projeler (ad, aciklama) VALUES (?, ?)", (ad, aciklama))
    proje_id = cur.lastrowid
    # Otomatik ilk bölüm
    db.execute("INSERT INTO bolumler (proje_id, ad, sira, icerik) VALUES (?, ?, 1, ?)",
               (proje_id, "Bölüm 1", json.dumps([])))
    db.commit()
    return jsonify({"ok": True, "id": proje_id})


@app.route("/api/proje/<int:proje_id>", methods=["GET"])
def proje_getir(proje_id):
    db = get_db()
    proje = db.execute("SELECT id, ad, aciklama FROM projeler WHERE id=?", (proje_id,)).fetchone()
    if not proje:
        return jsonify({"hata": "bulunamadı"}), 404
    bolumler = db.execute(
        "SELECT id, ad, sira FROM bolumler WHERE proje_id=? ORDER BY sira, id",
        (proje_id,),
    ).fetchall()
    karakterler = db.execute(
        "SELECT id, ad, aciklama FROM karakterler WHERE proje_id=? ORDER BY ad",
        (proje_id,),
    ).fetchall()
    mekanlar = db.execute(
        "SELECT id, ad, aciklama FROM mekanlar WHERE proje_id=? ORDER BY ad",
        (proje_id,),
    ).fetchall()
    return jsonify({
        "proje": dict(proje),
        "bolumler": [dict(b) for b in bolumler],
        "karakterler": [dict(k) for k in karakterler],
        "mekanlar": [dict(m) for m in mekanlar],
    })


@app.route("/api/proje/<int:proje_id>", methods=["PATCH"])
def proje_guncelle(proje_id):
    data = request.get_json(force=True)
    db = get_db()
    if "ad" in data:
        db.execute("UPDATE projeler SET ad=? WHERE id=?", (data["ad"].strip() or "İsimsiz", proje_id))
    if "aciklama" in data:
        db.execute("UPDATE projeler SET aciklama=? WHERE id=?", (data["aciklama"], proje_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/proje/<int:proje_id>", methods=["DELETE"])
def proje_sil(proje_id):
    db = get_db()
    db.execute("DELETE FROM projeler WHERE id=?", (proje_id,))
    db.commit()
    return jsonify({"ok": True})


# --- Bölümler ---

@app.route("/api/proje/<int:proje_id>/bolum", methods=["POST"])
def bolum_ekle(proje_id):
    data = request.get_json(force=True)
    ad = (data.get("ad") or "").strip()
    db = get_db()
    max_sira = db.execute(
        "SELECT COALESCE(MAX(sira), 0) AS s FROM bolumler WHERE proje_id=?",
        (proje_id,),
    ).fetchone()["s"]
    if not ad:
        ad = f"Bölüm {max_sira + 1}"
    cur = db.execute(
        "INSERT INTO bolumler (proje_id, ad, sira, icerik) VALUES (?, ?, ?, ?)",
        (proje_id, ad, max_sira + 1, json.dumps([])),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "ad": ad})


@app.route("/api/bolum/<int:bolum_id>", methods=["GET"])
def bolum_getir(bolum_id):
    db = get_db()
    row = db.execute(
        "SELECT id, proje_id, ad, sira, icerik FROM bolumler WHERE id=?",
        (bolum_id,),
    ).fetchone()
    if not row:
        return jsonify({"hata": "bulunamadı"}), 404
    try:
        bloklar = json.loads(row["icerik"]) if row["icerik"] else []
    except (json.JSONDecodeError, TypeError):
        bloklar = []
    return jsonify({
        "id": row["id"], "proje_id": row["proje_id"], "ad": row["ad"],
        "sira": row["sira"], "bloklar": bloklar,
    })


@app.route("/api/bolum/<int:bolum_id>", methods=["PATCH"])
def bolum_guncelle(bolum_id):
    data = request.get_json(force=True)
    db = get_db()
    row = db.execute("SELECT proje_id FROM bolumler WHERE id=?", (bolum_id,)).fetchone()
    if not row:
        return jsonify({"hata": "bulunamadı"}), 404
    proje_id = row["proje_id"]
    if "ad" in data:
        db.execute("UPDATE bolumler SET ad=?, guncelleme=CURRENT_TIMESTAMP WHERE id=?",
                   (data["ad"].strip() or "Bölüm", bolum_id))
    if "bloklar" in data:
        # Geçerli tipleri filtrele
        temiz = []
        for b in data["bloklar"]:
            tip = b.get("tip")
            if tip not in BLOK_TIPLERI:
                tip = "aksiyon"
            temiz.append({
                "tip": tip,
                "metin": str(b.get("metin", "")),
                "beat": str(b.get("beat", "")) if b.get("beat") else "",
            })
        icerik_json = json.dumps(temiz, ensure_ascii=False)
        db.execute("UPDATE bolumler SET icerik=?, guncelleme=CURRENT_TIMESTAMP WHERE id=?",
                   (icerik_json, bolum_id))
        db.commit()
        # Karakter/mekan otomatik tanıma
        _ozumle_karakter_mekan(proje_id, temiz)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/bolum/<int:bolum_id>", methods=["DELETE"])
def bolum_sil(bolum_id):
    db = get_db()
    db.execute("DELETE FROM bolumler WHERE id=?", (bolum_id,))
    db.commit()
    return jsonify({"ok": True})


# --- Karakter / Mekan açıklamaları ---

@app.route("/api/karakter/<int:karakter_id>", methods=["PATCH"])
def karakter_guncelle(karakter_id):
    data = request.get_json(force=True)
    db = get_db()
    if "aciklama" in data:
        db.execute("UPDATE karakterler SET aciklama=? WHERE id=?",
                   (data["aciklama"], karakter_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/karakter/<int:karakter_id>", methods=["DELETE"])
def karakter_sil(karakter_id):
    db = get_db()
    db.execute("DELETE FROM karakterler WHERE id=?", (karakter_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/mekan/<int:mekan_id>", methods=["PATCH"])
def mekan_guncelle(mekan_id):
    data = request.get_json(force=True)
    db = get_db()
    if "aciklama" in data:
        db.execute("UPDATE mekanlar SET aciklama=? WHERE id=?",
                   (data["aciklama"], mekan_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/mekan/<int:mekan_id>", methods=["DELETE"])
def mekan_sil(mekan_id):
    db = get_db()
    db.execute("DELETE FROM mekanlar WHERE id=?", (mekan_id,))
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    print(f"Senaryo v1 çalışıyor: http://127.0.0.1:5002 (DB: {DB_PATH})")
    app.run(debug=True, port=5002)
