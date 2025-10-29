import sqlite3
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
import streamlit as st

# ============== GENEL =================
st.set_page_config(page_title="Sarıkaya Kuyumculuk", layout="wide")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
DB = "data.db"

def db():
    return sqlite3.connect(DB, check_same_thread=False)

def run(sql, p=()):
    with db() as c:
        c.execute(sql, p); c.commit()

def q(sql, p=()):
    with db() as c:
        return pd.read_sql_query(sql, c, params=p)

# ============== ŞEMA ==================
def ensure_schema():
    # Açılış bakiyeleri (HAS & TL)
    run("""CREATE TABLE IF NOT EXISTS opening_balances(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, tl REAL DEFAULT 0.0, has REAL DEFAULT 0.0, note TEXT)""")

    # Kasa Defteri (TL & HAS hareketleri – tahsilat/ödeme vs.)
    run("""CREATE TABLE IF NOT EXISTS cash_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, ttype TEXT, party TEXT,
            product TEXT, qty REAL, unit TEXT,
            unit_price REAL,           -- TL (isteğe bağlı)
            tl_amount REAL DEFAULT 0.0,  -- + tahsilat / - ödeme
            has_amount REAL DEFAULT 0.0, -- + alacak / - borç (HAS)
            note TEXT)""")

    # Envanter hareketleri (alış/satış/düzeltme)
    run("""CREATE TABLE IF NOT EXISTS inventory_moves(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, move_type TEXT, product TEXT,
            qty REAL, unit TEXT, note TEXT)""")

    # Ürün maliyeti (HAS bazında maliyet) – tedarik kaynağı ile
    run("""CREATE TABLE IF NOT EXISTS product_costs(
            product TEXT PRIMARY KEY,
            has_cost_per_unit REAL NOT NULL,  -- 1 adet/gram almak için kaç HAS veriyorum?
            source TEXT, ts TEXT)""")

    # Envanter anındaki kur (₺ / 1 HAS)
    run("""CREATE TABLE IF NOT EXISTS has_rates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, tr_per_has REAL NOT NULL)""")

    # Müşteri borç/alacak (gram 24k karşılığı)
    run("""CREATE TABLE IF NOT EXISTS customer_grams(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, name TEXT, grams REAL,    -- + alacak, - borç
            note TEXT)""")

    # Emanet altınlar (kasada devir daim eden)
    run("""CREATE TABLE IF NOT EXISTS consigned_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, owner TEXT, product TEXT,
            qty REAL, unit TEXT, direction TEXT,  -- 'in' emanet giriş, 'out' iade/çıkış
            note TEXT)""")

    # Özbağ net bakiye (HAS) – tek satır
    run("""CREATE TABLE IF NOT EXISTS ozbag_balance(
            id INTEGER PRIMARY KEY CHECK(id=1),
            has_net REAL NOT NULL)""")
    if q("SELECT COUNT(*) n FROM ozbag_balance").iloc[0,0] == 0:
        run("INSERT INTO ozbag_balance(id,has_net) VALUES(1,0.0)")

ensure_schema()

# ============== ÜRÜN REHBERİ =========
@st.cache_data
def catalog() -> Dict[str, dict]:
    return {
        "Çeyrek Altın":     {"unit":"adet","has_factor":0.3520},
        "Yarım Altın":      {"unit":"adet","has_factor":0.7040},
        "Tam Altın":        {"unit":"adet","has_factor":1.4080},
        "Ata Lira":         {"unit":"adet","has_factor":1.4160},
        "24 Ayar Gram":     {"unit":"gr",  "has_factor":1.0000},
        "22 Ayar Gram":     {"unit":"gr",  "has_factor":0.9160},
        "22 Ayar 0,5 gr":   {"unit":"adet","has_factor":0.4580},
        "22 Ayar 0,25 gr":  {"unit":"adet","has_factor":0.2290},
        "Hurda Bilezik 22K":{"unit":"gr",  "has_factor":0.9160},  # varsayılan mil
    }
CAT = catalog()
PRODUCTS = list(CAT.keys())

def has_equiv(product:str, qty:float)->float:
    return round(qty * CAT[product]["has_factor"], 6)

def latest_has_rate()->Optional[float]:
    df = q("SELECT tr_per_has FROM has_rates ORDER BY id DESC LIMIT 1")
    return float(df.iloc[0,0]) if not df.empty else None

def get_cost(product:str)->Optional[float]:
    df = q("SELECT has_cost_per_unit FROM product_costs WHERE product=?",(product,))
    return float(df.iloc[0,0]) if not df.empty else None

# ============== ÜST MENÜ =============
st.title("💎 Sarıkaya Kuyumculuk — Kasa • Envanter • Maliyet")

tabs = st.tabs([
    "📦 Açılış & Özet",
    "🧾 İşlemler (Alış/Satış/Ödeme/Tahsilat)",
    "🏷️ Maliyet & Kur",
    "📋 Envanter Sayımı",
    "🏦 Özbağ & Emanet",
])

# ---------- 1) Açılış & Özet ----------
with tabs[0]:
    st.subheader("Açılış Bakiyeleri")
    col_a, col_b, col_c = st.columns([1,1,2])
    with col_a:
        tl_open = st.number_input("Açılış TL", min_value=0.0, step=100.0, key="open_tl")
    with col_b:
        has_open = st.number_input("Açılış HAS", min_value=0.0, step=1.0, key="open_has")
    with col_c:
        note_open = st.text_input("Not", key="open_note")
    if st.button("Açılış kaydet", key="btn_open"):
        run("INSERT INTO opening_balances(ts,tl,has,note) VALUES(?,?,?,?)",
            (NOW, tl_open, has_open, note_open))
        st.success("Açılış güncellendi.")

    st.markdown("### Toplam Bakiyeler")
    tl0, has0 = 0.0, 0.0
    df_open = q("SELECT tl,has FROM opening_balances")
    if not df_open.empty:
        tl0 = float(df_open["tl"].sum())
        has0 = float(df_open["has"].sum())

    df_cash = q("SELECT tl_amount,has_amount FROM cash_ledger")
    tl_sum = float(df_cash["tl_amount"].sum()) if not df_cash.empty else 0.0
    has_sum = float(df_cash["has_amount"].sum()) if not df_cash.empty else 0.0

    # Özbağ net pozisyonu
    ozbag = q("SELECT has_net FROM ozbag_balance").iloc[0,0]

    c1,c2,c3 = st.columns(3)
    c1.metric("Kasa TL", f"{tl0 + tl_sum:,.2f} ₺")
    c2.metric("Kasa HAS", f"{has0 + has_sum:,.3f} HAS")
    c3.metric("Özbağ Net (HAS)", f"{ozbag:,.3f} HAS")

    st.caption("Not: Özbağ Net (+) = Özbağ size borçlu, (-) = sizin Özbağ'a borcunuz.")

# ---------- 2) İşlemler ----------
with tabs[1]:
    st.subheader("İşlem Girişi")
    tcol1,tcol2,tcol3 = st.columns(3)
    with tcol1:
        ttype = st.selectbox("Tür", [
            "alış (müşteriden)", "satış (müşteriye)",
            "tahsilat (TL)", "ödeme (TL)",
            "müşteri not (gram)", "envanter düzeltme"
        ], key="tr_type")
    with tcol2:
        product = st.selectbox("Ürün", PRODUCTS, key="tr_product")
    with tcol3:
        qty = st.number_input("Adet / Gram", min_value=0.0, step=1.0, key="tr_qty")

    ucol1, ucol2, ucol3 = st.columns(3)
    with ucol1:
        unit = CAT[product]["unit"]
        st.text_input("Birim", value=unit, disabled=True, key="tr_unit_ro")
    with ucol2:
        unit_price = st.number_input("Birim Fiyat (TL) (opsiyonel)", min_value=0.0, step=1.0, key="tr_uprice")
    with ucol3:
        party = st.text_input("Müşteri/Taraf (ops.)", key="tr_party")

    note = st.text_input("Not", key="tr_note")

    if st.button("Kaydet", key="btn_tr_save"):
        has_mov = 0.0
        tl_mov = 0.0
        move_type = None

        if ttype == "alış (müşteriden)":
            move_type = "purchase"
            # müşteriden ürün aldık → stok +, TL - (istersek); HAS defteri: - (müşteriye borçlanma yoksa 0)
            # burada sade: envantere giriş
            run("""INSERT INTO inventory_moves(ts,move_type,product,qty,unit,note)
                   VALUES(?,?,?,?,?,?)""",(NOW,move_type,product,qty,unit,note))
            # TL ödeme girişi (negatif kasa)
            if unit_price>0:
                tl_mov = -(qty*unit_price)
            run("""INSERT INTO cash_ledger(ts,ttype,party,product,qty,unit,unit_price,tl_amount,has_amount,note)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (NOW,"purchase",party,product,qty,unit,unit_price,tl_mov,0.0,note))

        elif ttype == "satış (müşteriye)":
            move_type = "sale"
            run("""INSERT INTO inventory_moves(ts,move_type,product,qty,unit,note)
                   VALUES(?,?,?,?,?,?)""",(NOW,move_type,product,-qty,unit,note))
            if unit_price>0:
                tl_mov = +(qty*unit_price)
            run("""INSERT INTO cash_ledger(ts,ttype,party,product,qty,unit,unit_price,tl_amount,has_amount,note)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (NOW,"sale",party,product,qty,unit,unit_price,tl_mov,0.0,note))

        elif ttype == "tahsilat (TL)":
            tl_mov = +qty
            run("""INSERT INTO cash_ledger(ts,ttype,party,tl_amount,note)
                   VALUES(?,?,?,?,?)""",(NOW,"collection",party,tl_mov,note))

        elif ttype == "ödeme (TL)":
            tl_mov = -qty
            run("""INSERT INTO cash_ledger(ts,ttype,party,tl_amount,note)
                   VALUES(?,?,?,?,?)""",(NOW,"payment",party,tl_mov,note))

        elif ttype == "müşteri not (gram)":
            # +grams = müşteriden ALACAK, -grams = müşteriye BORÇ
            run("""INSERT INTO customer_grams(ts,name,grams,note)
                   VALUES(?,?,?,?)""", (NOW, party or "-", qty, note))

        elif ttype == "envanter düzeltme":
            move_type = "adjust"
            run("""INSERT INTO inventory_moves(ts,move_type,product,qty,unit,note)
                   VALUES(?,?,?,?,?,?)""",(NOW,move_type,product,qty,unit,note))

        st.success("İşlem kaydedildi.")

    st.markdown("#### Son İşlemler")
    st.dataframe(q("""SELECT ts, ttype, party, product, qty, unit, unit_price, tl_amount, has_amount, note
                      FROM cash_ledger ORDER BY id DESC LIMIT 50"""),
                 use_container_width=True)

# ---------- 3) Maliyet & Kur ----------
with tabs[2]:
    st.subheader("Ürün Maliyeti (HAS) & Envanter Kuru")
    st.caption("Çeyrek gibi ürünlerde **1 adet almak için kaç HAS** verdiğinizi girin. Kaynak: Özbağ veya manuel.")

    c1,c2,c3 = st.columns(3)
    with c1:
        p_sel = st.selectbox("Ürün seç", PRODUCTS, key="cost_p")
    with c2:
        d_cur = get_cost(p_sel) or has_equiv(p_sel,1.0)  # yoksa default HAS içeriğine eşitle
        cost_has = st.number_input("1 birim için HAS maliyeti", min_value=0.0, value=float(d_cur), step=0.001, key="cost_val")
    with c3:
        src = st.selectbox("Kaynak", ["Özbağ","Manuel"], key="cost_src")
    if st.button("Maliyeti Kaydet", key="cost_save"):
        run("""INSERT INTO product_costs(product,has_cost_per_unit,source,ts)
               VALUES(?,?,?,?)
               ON CONFLICT(product) DO UPDATE SET has_cost_per_unit=excluded.has_cost_per_unit,
                                                 source=excluded.source, ts=excluded.ts""",
            (p_sel, cost_has, src, NOW))
        st.success("Maliyet güncellendi.")

    st.markdown("##### Tanımlı Maliyetler")
    st.dataframe(q("SELECT product, has_cost_per_unit, source, ts FROM product_costs ORDER BY product"),
                 use_container_width=True)

    st.divider()
    hr = latest_has_rate() or 0.0
    new_rate = st.number_input("HAS kuru (₺ / 1 HAS)", min_value=0.0, value=float(hr), step=1.0, key="has_rate")
    if st.button("Kuru Kaydet", key="rate_save"):
        run("INSERT INTO has_rates(ts,tr_per_has) VALUES(?,?)", (NOW, new_rate))
        st.success("HAS kuru kaydedildi.")

# ---------- 4) Envanter Sayımı ----------
with tabs[3]:
    st.subheader("Günlük Envanter Sayımı & Değerleme")
    rate = latest_has_rate()
    if not rate:
        st.warning("Önce **Maliyet & Kur** sekmesinden bir **HAS kuru** girin.")
    else:
        st.info(f"Kullanılan HAS kuru: **{rate:,.2f} ₺**")

    st.caption("Her ürün için saydığınız miktarı girin; değerleme ürüne tanımlı **HAS maliyeti** ve güncel kurla yapılır.")

    rows = []
    total_has_cost = 0.0
    total_tl_cost = 0.0
    for p in PRODUCTS:
        cols = st.columns([3,2,2,2,2], vertical_alignment="center")
        qty_count = cols[0].number_input(f"{p} sayım", min_value=0.0, step=1.0, key=f"inv_qty_{p}")
        unit = CAT[p]["unit"]
        cols[1].text_input("Birim", value=unit, disabled=True, key=f"inv_unit_{p}")
        # ürün maliyeti (HAS)
        p_cost = get_cost(p) or has_equiv(p,1.0)
        cols[2].number_input("HAS maliyeti/birim", min_value=0.0, value=float(p_cost), step=0.001, key=f"inv_cost_{p}", disabled=True)
        has_val = qty_count * p_cost
        tl_val = has_val * (rate or 0.0)
        cols[3].text_input("HAS toplam", value=f"{has_val:,.3f}", disabled=True, key=f"inv_has_{p}")
        cols[4].text_input("TL toplam", value=f"{tl_val:,.2f}", disabled=True, key=f"inv_tl_{p}")

        rows.append((p, qty_count, unit, p_cost, has_val, tl_val))
        total_has_cost += has_val
        total_tl_cost += tl_val

    st.divider()
    st.metric("Toplam HAS (maliyet)", f"{total_has_cost:,.3f} HAS")
    st.metric("Toplam TL (maliyet)", f"{total_tl_cost:,.2f} ₺")
    st.caption("Not: Bu ekran **sayım fotoğrafı** gibidir; isterseniz ayrıca düzeltme hareketi olarak kaydedebilirsiniz.")

# ---------- 5) Özbağ & Emanet ----------
with tabs[4]:
    st.subheader("Özbağ İşlemleri (Hurda Bilezik Alımı / Mahsup)")
    oc1, oc2 = st.columns(2)

    with oc1:
        st.markdown("##### Hurda Bilezik 22K **Alım** (Özbağ’a gönderilecek)")
        hb_qty = st.number_input("Miktar (gr)", min_value=0.0, step=1.0, key="hb_qty")
        hb_note = st.text_input("Not", key="hb_note")
        if st.button("Hurda Bilezik Al / Stoka Ekle", key="hb_btn"):
            # Envantere +, Kasa hareketi yok (mahsup için ayrı)
            run("""INSERT INTO inventory_moves(ts,move_type,product,qty,unit,note)
                   VALUES(?,?,?,?,?,?)""", (NOW,"scrap_in","Hurda Bilezik 22K",hb_qty,"gr",hb_note))
            st.success("Hurda bilezik envantere alındı.")

    with oc2:
        st.markdown("##### Özbağ **Mahsup** (hurda gönder → ürün al / borç kapat)")
        mcol1, mcol2 = st.columns(2)
        with mcol1:
            get_prod = st.selectbox("Aldığın ürün", PRODUCTS, index=PRODUCTS.index("Çeyrek Altın"), key="oz_get_p")
            get_qty  = st.number_input("Aldığın miktar", min_value=0.0, step=1.0, key="oz_get_q")
        with mcol2:
            give_scrap = st.number_input("Gönderilen Hurda (gr)", min_value=0.0, step=1.0, key="oz_give_scrap")
            oz_note = st.text_input("Not", key="oz_note")

        if st.button("Mahsup Yap", key="oz_settle"):
            # 1) hurda çıkışı (envanter -)
            run("""INSERT INTO inventory_moves(ts,move_type,product,qty,unit,note)
                   VALUES(?,?,?,?,?,?)""", (NOW,"supplier_out","Hurda Bilezik 22K",-give_scrap,"gr",oz_note))
            # 2) ürün girişi (envanter +)
            run("""INSERT INTO inventory_moves(ts,move_type,product,qty,unit,note)
                   VALUES(?,?,?,?,?,?)""", (NOW,"supplier_in",get_prod,get_qty,CAT[get_prod]["unit"],oz_note))
            # 3) Özbağ net HAS güncelle (gönderilen hurdanın HAS karşılığı -; alınan ürünün tedarik HAS maliyeti +)
            scrap_has = give_scrap * CAT["Hurda Bilezik 22K"]["has_factor"]
            prod_has_cost = get_cost(get_prod) or has_equiv(get_prod,1.0)
            delta = prod_has_cost*get_qty - scrap_has  # (+) Özbağ bize borçlu, (-) biz Özbağ'a
            cur = q("SELECT has_net FROM ozbag_balance").iloc[0,0]
            run("UPDATE ozbag_balance SET has_net=?", (cur + delta,))
            st.success(f"Mahsup tamam: Özbağ net değişim {delta:+.3f} HAS")

    st.divider()
    st.subheader("Emanet (Kasada devir daim eden)")
    e1, e2, e3, e4 = st.columns(4)
    with e1:
        em_owner = st.text_input("İsim Soyisim", key="em_name")
    with e2:
        em_product = st.selectbox("Ürün", PRODUCTS, key="em_prod")
    with e3:
        em_qty = st.number_input("Adet/Gram", min_value=0.0, step=1.0, key="em_qty")
    with e4:
        em_dir = st.selectbox("Yön", ["in (emanet alındı)","out (emanet iade)"], key="em_dir")
    em_note = st.text_input("Not", key="em_note")
    if st.button("Emanet Kaydet", key="em_save"):
        direction = "in" if em_dir.startswith("in") else "out"
        run("""INSERT INTO consigned_items(ts,owner,product,qty,unit,direction,note)
               VALUES(?,?,?,?,?,?,?)""",
            (NOW,em_owner,em_product,em_qty,CAT[em_product]["unit"],direction,em_note))
        st.success("Emanet hareketi kaydedildi.")

    st.markdown("##### Emanet Özeti")
    df_em = q("""SELECT owner, product,
                        SUM(CASE WHEN direction='in'  THEN qty ELSE 0 END) AS giren,
                        SUM(CASE WHEN direction='out' THEN qty ELSE 0 END) AS cikan,
                        SUM(CASE WHEN direction='in'  THEN qty ELSE 0 END)
                      - SUM(CASE WHEN direction='out' THEN qty ELSE 0 END) AS bakiye,
                        MAX(ts) AS son_hareket
                 FROM consigned_items
                 GROUP BY owner, product
                 ORDER BY owner, product""")
    st.dataframe(df_em, use_container_width=True)

    st.divider()
    st.subheader("Müşteri Borç / Alacak (Gram 24k karşılığı)")
    cna, cng = st.columns(2)
    with cna:
        cust = st.text_input("İsim Soyisim", key="cg_name")
    with cng:
        grams = st.number_input("Gram (+ alacak, - borç)", step=0.001, key="cg_grams")
    cg_note = st.text_input("Not", key="cg_note")
    if st.button("Borç/Alacak Kaydet", key="cg_save"):
        run("INSERT INTO customer_grams(ts,name,grams,note) VALUES(?,?,?,?)", (NOW,cust,grams,cg_note))
        st.success("Kayıt eklendi.")
    st.markdown("##### Özet")
    st.dataframe(q("""SELECT name, SUM(grams) AS net_grams
                      FROM customer_grams GROUP BY name ORDER BY name"""),
                 use_container_width=True)