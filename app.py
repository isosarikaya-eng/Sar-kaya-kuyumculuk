# -*- coding: utf-8 -*-
import sqlite3
from contextlib import closing
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import streamlit as st

# ---------------------------
# Yardımcı / Sabitler
# ---------------------------
DB_PATH = "sarikaya.db"

TL = "TL"
HAS = "HAS"

PRODUCTS = [
    # kod, görünen ad, stok birimi, has_katsayısı
    ("CEYREK", "Eski Çeyrek Altın", TL, Decimal("0")),   # adet bazlı
    ("YARIM",  "Eski Yarım Altın",  TL, Decimal("0")),
    ("TAM",    "Eski Tam Altın",    TL, Decimal("0")),
    ("ATA",    "Eski Ata Lira",     TL, Decimal("0")),
    ("G24",    "24 Ayar Gram",      TL, Decimal("1.0000")),   # gram x 1.0000 = has gram
    ("G22",    "22 Ayar Gram",      TL, Decimal("0.9160")),   # varsayılan 22k
    ("G22_05", "22 Ayar 0,5 gr",    TL, Decimal("0.9160")),
    ("G22_025","22 Ayar 0,25 gr",   TL, Decimal("0.9160")),
    ("SCR22",  "22 Ayar Hurda Bilezik", TL, Decimal("0.9140")), # hurda girişinde milYem değişebilir
]

MARGINS = {
    # sadece “öneri” göstermek istersen — fiyat girerken referans olur, zorlama yok
    "CEYREK": {"alis": -50,  "satis": +50},
    "YARIM":  {"alis": -100, "satis": +100},
    "TAM":    {"alis": -200, "satis": +200},
    "ATA":    {"alis": -200, "satis": +200},
}

def tl(x):
    if x is None: return ""
    return f"{Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}".replace(",", "X").replace(".", ",").replace("X",".")

def dec(x) -> Decimal:
    if isinstance(x, Decimal): return x
    if x is None or x == "": return Decimal("0")
    return Decimal(str(x))

def now_ts():
    return datetime.now().isoformat(timespec="seconds")

# ---------------------------
# DB Kurulum
# ---------------------------
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        # ürün kartı
        cur.execute("""
        CREATE TABLE IF NOT EXISTS products(
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            has_factor TEXT NOT NULL
        )
        """)
        # açılış stok
        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening_stock(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT NOT NULL,
            qty REAL NOT NULL DEFAULT 0,
            amount_tl REAL NOT NULL DEFAULT 0,
            note TEXT,
            ts TEXT NOT NULL
        )
        """)
        # kasa açılışı
        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening_cash(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount_tl REAL NOT NULL DEFAULT 0,
            note TEXT,
            ts TEXT NOT NULL
        )
        """)
        # işlemler: alış, satış, hurda_giris, iade, fiyat değişimi vs
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trx(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tdate TEXT NOT NULL,           -- YYYY-MM-DD
            ttype TEXT NOT NULL,           -- ALIS / SATIS / HURDA_IN / SUPPLY_OUT / SUPPLY_IN / CASH_IN / CASH_OUT
            product_code TEXT,             -- nakit işlemlerde boş kalabilir
            qty REAL DEFAULT 0,            -- adet/gram
            unit_price_tl REAL DEFAULT 0,  -- birim TL (isteğe bağlı)
            amount_tl REAL DEFAULT 0,      -- toplam TL (negatif olabilir)
            note TEXT,
            counterparty TEXT,             -- müşteri/tedarikçi adı (ÖZBAĞ gibi)
            has_gram REAL DEFAULT 0,       -- hurda / tedarik işlemlerinde HAS gram
            milem REAL,                    -- hurda girişinde kullanılan milyem (ör. 0.9140)
            ts TEXT NOT NULL
        )
        """)
        # tedarikçi ekstre (özbağ)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sname TEXT NOT NULL,          -- tedarikçi adı (ÖZBAĞ)
            ldate TEXT NOT NULL,          -- YYYY-MM-DD
            direction TEXT NOT NULL,      -- BORC / ALACAK  (tedarikçi açısından) 
            currency TEXT NOT NULL,       -- TL / HAS
            amount REAL NOT NULL DEFAULT 0,
            note TEXT,
            ts TEXT NOT NULL
        )
        """)
        # borç-tahsil takip (müşteriler için)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS receivables(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cname TEXT NOT NULL,          -- isim soyisim
            rdate TEXT NOT NULL,
            direction TEXT NOT NULL,      -- BORC / ALACAK  (müşteri açısından)
            currency TEXT NOT NULL,       -- TL / HAS
            amount REAL NOT NULL DEFAULT 0,
            note TEXT,
            ts TEXT NOT NULL
        )
        """)
        con.commit()

        # ürün tablosunu besle
        cur.execute("SELECT COUNT(*) FROM products")
        if cur.fetchone()[0] == 0:
            for code, name, unit, factor in PRODUCTS:
                cur.execute("INSERT INTO products(code,name,unit,has_factor) VALUES (?,?,?,?)",
                            (code, name, unit, str(factor)))
            con.commit()

init_db()

# ---------------------------
# Veri Fonksiyonları
# ---------------------------
def df(sql, params=()):
    with closing(sqlite3.connect(DB_PATH)) as con:
        return pd.read_sql_query(sql, con, params=params)

def execute(sql, params=()):
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute(sql, params)
        con.commit()

def product_df():
    d = df("SELECT code,name,unit,has_factor FROM products")
    d["has_factor"] = d["has_factor"].astype(float)
    return d

def stock_snapshot():
    """Açılış + işlemlerden anlık stok."""
    p = product_df().set_index("code")
    p["qty"] = 0.0
    p["cost_tl"] = 0.0

    op = df("SELECT product_code, qty, amount_tl FROM opening_stock")
    for _, r in op.iterrows():
        p.at[r.product_code, "qty"] += float(r.qty)
        p.at[r.product_code, "cost_tl"] += float(r.amount_tl)

    # ALIS stok ekler, SATIS düşer, HURDA_IN hurda stok ekler (SCR22)
    trx = df("SELECT ttype, product_code, qty, amount_tl FROM trx WHERE product_code IS NOT NULL")
    for _, r in trx.iterrows():
        code = r.product_code
        if code not in p.index: 
            continue
        q = float(r.qty or 0)
        amt = float(r.amount_tl or 0)
        if r.ttype == "ALIS" or r.ttype == "HURDA_IN" or r.ttype == "SUPPLY_IN":
            p.at[code, "qty"] += q
            p.at[code, "cost_tl"] += max(0.0, amt)  # maliyet eklenebilir
        elif r.ttype == "SATIS" or r.ttype == "SUPPLY_OUT":
            p.at[code, "qty"] -= q
            p.at[code, "cost_tl"] -= 0.0

    p["avg_cost_tl"] = p.apply(lambda r: (r.cost_tl / r.qty) if r.qty else 0.0, axis=1)
    p = p.reset_index().rename(columns={"index":"code"})
    return p[["code","name","qty","avg_cost_tl","cost_tl"]]

def cash_balance_tl():
    op = df("SELECT COALESCE(SUM(amount_tl),0) v FROM opening_cash").iloc[0]["v"]
    trx_in = df("SELECT COALESCE(SUM(amount_tl),0) v FROM trx WHERE ttype IN ('SATIS','CASH_IN')").iloc[0]["v"]
    trx_out= df("SELECT COALESCE(SUM(amount_tl),0) v FROM trx WHERE ttype IN ('ALIS','HURDA_IN','SUPPLY_IN','CASH_OUT')").iloc[0]["v"]
    return Decimal(op) + Decimal(trx_in) - Decimal(trx_out)

def supplier_balance(sname="ÖZBAĞ"):
    q = df("""
        SELECT currency, direction, COALESCE(SUM(amount),0) amt
        FROM supplier_ledger WHERE sname=?
        GROUP BY currency, direction
    """, (sname,))
    tl_borc = tl_alacak = Decimal("0")
    has_borc = has_alacak = Decimal("0")
    for _, r in q.iterrows():
        amt = Decimal(str(r.amt))
        if r.currency == TL:
            if r.direction == "BORC":  tl_borc += amt
            else:                      tl_alacak += amt
        else:
            if r.direction == "BORC":  has_borc += amt
            else:                      has_alacak += amt
    return {
        "TL": (tl_borc - tl_alacak),
        "HAS": (has_borc - has_alacak)
    }

def today_profit():
    # Sadece bugün yapılan SATIS - ALIS (TL) farkı (çok basit k/z)
    d = date.today().isoformat()
    s = df("SELECT COALESCE(SUM(amount_tl),0) v FROM trx WHERE tdate=? AND ttype='SATIS'", (d,)).iloc[0]["v"]
    a = df("SELECT COALESCE(SUM(amount_tl),0) v FROM trx WHERE tdate=? AND ttype='ALIS'", (d,)).iloc[0]["v"]
    return Decimal(s) - Decimal(a)

# ---------------------------
# UI
# ---------------------------
st.set_page_config(page_title="Sarıkaya Kuyumculuk", page_icon="💎", layout="centered")

st.title("💎 Sarıkaya Kuyumculuk")
st.caption("Günlük kâr/zarar – Envanter – 22 Ayar Hurda – Özbağ Bakiye")

tabs = st.tabs([
    "Açılış / Ayarlar",
    "Alış – Satış",
    "22 Ayar Hurda Girişi",
    "Tedarikçi (Özbağ) İşlemleri",
    "Kasa & Envanter",
    "Borç / Tahsilat (Müşteri)"
])

# ---------------------------
# 1) Açılış / Ayarlar
# ---------------------------
with tabs[0]:
    st.subheader("Açılış Stokları")
    p = product_df()
    p_options = {f"{r['name']} ({r['code']})": r["code"] for _, r in p.iterrows()}
    c1, c2 = st.columns(2)
    with c1:
        sel = st.selectbox("Ürün", list(p_options.keys()))
        qty = st.number_input("Miktar (adet/gr)", min_value=0.0, step=1.0)
    with c2:
        amt = st.number_input("Toplam Maliyet (₺)", min_value=0.0, step=100.0)
        note = st.text_input("Not", "")
    if st.button("Açılış Stok Ekle"):
        execute("INSERT INTO opening_stock(product_code,qty,amount_tl,note,ts) VALUES (?,?,?,?,?)",
                (p_options[sel], qty, amt, note, now_ts()))
        st.success("Eklendi.")

    st.divider()
    st.subheader("Açılış Kasası (₺)")
    cash_amt = st.number_input("Kasa Açılış Tutarı (₺)", min_value=0.0, step=100.0)
    if st.button("Kasa Açılışı Kaydet"):
        execute("INSERT INTO opening_cash(amount_tl, note, ts) VALUES (?,?,?)", (cash_amt, "Açılış", now_ts()))
        st.success("Kaydedildi.")

    st.divider()
    st.subheader("Ürün Kartları")
    st.dataframe(p, hide_index=True, use_container_width=True)

# ---------------------------
# 2) Alış – Satış
# ---------------------------
with tabs[1]:
    st.subheader("Alış / Satış İşlemi")
    p = product_df()
    p_options = {f"{r['name']} ({r['code']})": r for _, r in p.iterrows()}
    choice = st.selectbox("Ürün Seç", list(p_options.keys()))
    prod = p_options[choice]
    ttype = st.radio("İşlem Türü", ["SATIS","ALIS"], index=0, horizontal=True)

    cols = st.columns(3)
    with cols[0]:
        qty = st.number_input("Adet / Gram", min_value=0.0, step=1.0, value=1.0)
    with cols[1]:
        unit_price = st.number_input("Birim Fiyat (₺)", min_value=0.0, step=10.0)
    with cols[2]:
        cust = st.text_input("Müşteri / Not", "")

    # Öneri sadece çeyrek/yarım/tam/ata için marj tablosundan
    if prod["code"] in MARGINS:
        st.caption("İpucu: Eski ziynet için marj rehberi; TL fiyatını siz girersiniz.")
        st.info(f"Alış marjı: {MARGINS[prod['code']]['alis']} ₺ | Satış marjı: +{MARGINS[prod['code']]['satis']} ₺")

    total = Decimal(unit_price) * Decimal(qty)
    st.metric("Toplam", f"{tl(total)} ₺")

    if st.button("Kaydet"):
        execute("""
            INSERT INTO trx(tdate, ttype, product_code, qty, unit_price_tl, amount_tl, note, counterparty, ts)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (date.today().isoformat(), ttype, prod["code"], qty, unit_price, float(total), cust, cust, now_ts()))
        st.success("İşlem kaydedildi.")

# ---------------------------
# 3) 22 Ayar Hurda Girişi
# ---------------------------
with tabs[2]:
    st.subheader("22 Ayar Hurda Bilezik Girişi (HAS hesaplı)")
    st.caption("Hurda bilezik aldığında gram x milyem = HAS gram kaydı yapılır. Milyem aksi belirtilmezse 0.9140 kabul edilir.")

    hurda_qty = st.number_input("Hurda Net Gram", min_value=0.0, step=0.1)
    milem = st.number_input("Milyem (örn 0.9140)", min_value=0.8000, max_value=0.9500, value=0.9140, step=0.0001, format="%.4f")
    unit_price = st.number_input("Birim Fiyat (₺/gr)", min_value=0.0, step=10.0)
    total = Decimal(hurda_qty) * Decimal(unit_price)
    has_gram = Decimal(hurda_qty) * Decimal(str(milem))
    note = st.text_input("Not / Müşteri", "")

    c1, c2 = st.columns(2)
    with c1: st.metric("TOPLAM (₺)", tl(total))
    with c2: st.metric("HAS Gram", f"{has_gram:.2f}")

    if st.button("Hurda Girişini Kaydet"):
        # stok SCR22 ürününe miktar girişi; TL çıkışı kasa açısından ALIŞ sayılır
        execute("""
            INSERT INTO trx(tdate, ttype, product_code, qty, unit_price_tl, amount_tl, note, has_gram, milem, ts)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (date.today().isoformat(), "HURDA_IN", "SCR22", float(hurda_qty), float(unit_price), float(total), note, float(has_gram), float(milem), now_ts()))
        st.success("Hurda giriş kaydı alındı.")

    st.divider()
    st.subheader("Hurda Stok ve Son Kayıtlar")
    snap = stock_snapshot()
    st.dataframe(snap[snap["code"]=="SCR22"], hide_index=True, use_container_width=True)
    last = df("SELECT tdate,qty,unit_price_tl,amount_tl,has_gram,milem,note FROM trx WHERE ttype='HURDA_IN' ORDER BY id DESC LIMIT 50")
    st.dataframe(last, hide_index=True, use_container_width=True)

# ---------------------------
# 4) Tedarikçi (Özbağ) İşlemleri
# ---------------------------
with tabs[3]:
    st.subheader("Özbağ Tedarikçi İşlemleri")
    st.caption("Hurda HAS’ı Özbağ’a gönderip karşılığında ürün aldığında veya TL ödeme yaptığında burada kaydet.")

    sname = "ÖZBAĞ"
    mode = st.radio("İşlem", ["Hurda Gönder (HAS) → Özbağ BORÇ", "Özbağ’dan Ürün Al (Stoka Gir) → Özbağ ALACAK",
                               "Özbağ’a TL Ödeme → Özbağ ALACAK", "Özbağ’dan TL Tahsil → Özbağ BORÇ"], horizontal=False)

    if "Hurda" in mode:
        has_amt = st.number_input("Gönderilen HAS (gr)", min_value=0.0, step=0.1)
        note = st.text_input("Not", "")
        if st.button("Kaydet (HAS Gönder)"):
            # tedarikçiye BORÇ (onların bize hakkı doğar) – HAS para birimi
            execute("""
                INSERT INTO supplier_ledger(sname, ldate, direction, currency, amount, note, ts)
                VALUES (?,?,?,?,?,?,?)
            """, (sname, date.today().isoformat(), "BORC", HAS, float(has_amt), note, now_ts()))
            # aynı anda hurda stoktan düş (tedarikçiye gönderildiği için)
            execute("""
                INSERT INTO trx(tdate, ttype, product_code, qty, unit_price_tl, amount_tl, note, has_gram, ts)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (date.today().isoformat(), "SUPPLY_OUT", "SCR22", float(has_amt/Decimal("0.9140")), 0.0, 0.0, f"Özbağ’a {has_amt} HAS gönderildi", float(has_amt), now_ts()))
            st.success("Kaydedildi.")

    elif "Ürün Al" in mode:
        prod_map = {f"{r['name']} ({r['code']})": r for _, r in product_df().iterrows() if r["code"]!="SCR22"}
        sel = st.selectbox("Ürün", list(prod_map.keys()))
        item = prod_map[sel]
        qty = st.number_input("Miktar (adet/gram)", min_value=0.0, step=1.0)
        unit_price = st.number_input("Birim Fiyat (₺)", min_value=0.0, step=10.0)
        note = st.text_input("Not", "")
        total = Decimal(qty) * Decimal(unit_price)
        st.metric("Toplam", f"{tl(total)} ₺")

        if st.button("Kaydet (Ürün Girişi)"):
            # stok girişi
            execute("""
                INSERT INTO trx(tdate, ttype, product_code, qty, unit_price_tl, amount_tl, note, counterparty, ts)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (date.today().isoformat(), "SUPPLY_IN", item["code"], qty, unit_price, float(total), note, sname, now_ts()))
            # tedarikçiye ALACAK (borcumuz azalır) TL cinsinden
            execute("""
                INSERT INTO supplier_ledger(sname, ldate, direction, currency, amount, note, ts)
                VALUES (?,?,?,?,?,?,?)
            """, (sname, date.today().isoformat(), "ALACAK", TL, float(total), f"{item['name']} {qty} birim", now_ts()))
            st.success("Kaydedildi.")

    elif "TL Ödeme" in mode:
        amt = st.number_input("Ödeme Tutarı (₺)", min_value=0.0, step=100.0)
        note = st.text_input("Not", "")
        if st.button("Kaydet (Ödeme)"):
            execute("""
                INSERT INTO supplier_ledger(sname, ldate, direction, currency, amount, note, ts)
                VALUES (?,?,?,?,?,?,?)
            """, (sname, date.today().isoformat(), "ALACAK", TL, float(amt), note, now_ts()))
            # kasa çıkışı
            execute("""
                INSERT INTO trx(tdate, ttype, amount_tl, note, counterparty, ts)
                VALUES (?,?,?,?,?,?)
            """, (date.today().isoformat(), "CASH_OUT", float(amt), f"Özbağ ödemesi: {note}", sname, now_ts()))
            st.success("Kaydedildi.")

    else:  # TL Tahsil
        amt = st.number_input("Tahsilat (₺)", min_value=0.0, step=100.0)
        note = st.text_input("Not", "")
        if st.button("Kaydet (Tahsil)"):
            execute("""
                INSERT INTO supplier_ledger(sname, ldate, direction, currency, amount, note, ts)
                VALUES (?,?,?,?,?,?,?)
            """, (sname, date.today().isoformat(), "BORC", TL, float(amt), note, now_ts()))
            execute("""
                INSERT INTO trx(tdate, ttype, amount_tl, note, counterparty, ts)
                VALUES (?,?,?,?,?,?)
            """, (date.today().isoformat(), "CASH_IN", float(amt), f"Özbağ tahsilat: {note}", sname, now_ts()))
            st.success("Kaydedildi.")

    st.divider()
    st.subheader("Özbağ Bakiye")
    bal = supplier_balance()
    c1, c2 = st.columns(2)
    with c1: st.metric("Özbağ Bakiye (TL)", tl(bal["TL"]))
    with c2: st.metric("Özbağ Bakiye (HAS)", f"{Decimal(bal['HAS']).quantize(Decimal('0.01'))} gr")
    st.dataframe(df("SELECT ldate as tarih, direction as yon, currency as birim, amount as tutar, note as aciklama FROM supplier_ledger ORDER BY id DESC LIMIT 100"),
                 hide_index=True, use_container_width=True)

# ---------------------------
# 5) Kasa & Envanter
# ---------------------------
with tabs[4]:
    st.subheader("Kasa & Envanter Özeti")
    st.metric("Kasa (₺)", tl(cash_balance_tl()))
    st.metric("Bugünkü Kâr / Zarar (basit)", tl(today_profit()))
    st.markdown("### Stok")
    snap = stock_snapshot()
    snap["qty"] = snap["qty"].round(2)
    snap["avg_cost_tl"] = snap["avg_cost_tl"].round(2)
    snap["cost_tl"] = snap["cost_tl"].round(2)
    st.dataframe(snap.rename(columns={"code":"Kod","name":"Ürün","qty":"Miktar","avg_cost_tl":"Ort.Maliyet(₺)","cost_tl":"Toplam Maliyet(₺)"}),
                 hide_index=True, use_container_width=True)

# ---------------------------
# 6) Borç / Tahsilat (Müşteri)
# ---------------------------
with tabs[5]:
    st.subheader("Müşteri – Borç / Tahsilat")
    cname = st.text_input("İsim – Soyisim")
    dirm = st.radio("İşlem", ["BORC","ALACAK"], horizontal=True)
    curr = st.radio("Para Birimi", [TL, HAS], horizontal=True, index=0)
    amt = st.number_input("Tutar", min_value=0.0, step=10.0)
    note = st.text_input("Not", "")
    if st.button("Kaydet (Müşteri)"):
        execute("""
            INSERT INTO receivables(cname, rdate, direction, currency, amount, note, ts)
            VALUES (?,?,?,?,?,?,?)
        """, (cname, date.today().isoformat(), dirm, curr, float(amt), note, now_ts()))
        st.success("Kaydedildi.")

    st.divider()
    st.subheader("Müşteri Ekstresi")
    r = df("SELECT rdate as tarih, cname as isim, direction as yon, currency as birim, amount as tutar, note as aciklama FROM receivables ORDER BY id DESC LIMIT 200")
    st.dataframe(r, hide_index=True, use_container_width=True)