# -*- coding: utf-8 -*-
import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List

import pandas as pd
import streamlit as st

# =========================================================
# GENEL
# =========================================================
st.set_page_config(page_title="Sarıkaya Kuyumculuk — Satış & POS & Özbağ", layout="wide")
DB = "sarikaya_kuyum.db"
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------
# DB yardımcıları
# ---------------------------------------------------------
def conn():
    return sqlite3.connect(DB, check_same_thread=False)

def run(sql: str, params: tuple = ()):
    with conn() as c:
        c.execute(sql, params)
        c.commit()

def qdf(sql: str, params: tuple = ()) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(sql, c, params=params)


# ---------------------------------------------------------
# ŞEMA
# ---------------------------------------------------------
def ensure_schema():
    # Banka ayarları (POS komisyonları)
    run("""
    CREATE TABLE IF NOT EXISTS banks(
      name TEXT PRIMARY KEY,
      pos_sale_pct REAL NOT NULL,       -- normal satış POS komisyon %
      cash_adv_pct REAL NOT NULL,       -- kart->nakit tek geçiş komisyon %
      settle_days INTEGER NOT NULL      -- ertesi gün = 1
    )""")

    # Açılış bakiyeleri
    run("""
    CREATE TABLE IF NOT EXISTS openings(
      id INTEGER PRIMARY KEY CHECK(id=1),
      cash_tl REAL NOT NULL DEFAULT 0
    )""")
    if qdf("SELECT COUNT(*) n FROM openings").iloc[0,0] == 0:
        run("INSERT INTO openings(id,cash_tl) VALUES(1,0)")

    run("""
    CREATE TABLE IF NOT EXISTS bank_openings(
      bank TEXT PRIMARY KEY,
      balance_tl REAL NOT NULL DEFAULT 0,
      FOREIGN KEY(bank) REFERENCES banks(name)
    )""")

    # Ürün kataloğu (sabit listeyi ilk yüklemede ekleriz)
    run("""
    CREATE TABLE IF NOT EXISTS products(
      name TEXT PRIMARY KEY,
      unit TEXT NOT NULL        -- 'adet' veya 'gr'
    )""")

    # Satış başlığı
    run("""
    CREATE TABLE IF NOT EXISTS sales(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      sdate TEXT NOT NULL,
      customer TEXT,
      note TEXT
    )""")

    # Satış kalemleri
    run("""
    CREATE TABLE IF NOT EXISTS sale_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sale_id INTEGER NOT NULL,
      product TEXT NOT NULL,
      qty REAL NOT NULL,
      unit TEXT NOT NULL,
      unit_price REAL NOT NULL,
      line_total REAL NOT NULL,
      FOREIGN KEY(sale_id) REFERENCES sales(id)
    )""")

    # Ödeme bacakları (parçalı)
    # method: CASH / TRANSFER / CARD / CASH_ADV
    run("""
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      pdate TEXT NOT NULL,
      sale_id INTEGER,
      method TEXT NOT NULL,
      bank TEXT,
      gross_amount REAL NOT NULL,
      fee_pct REAL NOT NULL,
      fee_amount REAL NOT NULL,
      net_settlement REAL NOT NULL,
      settle_date TEXT,
      direction TEXT NOT NULL,         -- INFLOW / OUTFLOW
      note TEXT
    )""")

    # Kasa⇄Banka transferleri
    run("""
    CREATE TABLE IF NOT EXISTS transfers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      tdate TEXT NOT NULL,
      ttype TEXT NOT NULL,       -- CASH_TO_BANK / BANK_TO_CASH
      bank TEXT,
      amount REAL NOT NULL,
      note TEXT
    )""")

    # Envanter (basit stok takibi: ürün/gram/adet bazında stok hareket)
    run("""
    CREATE TABLE IF NOT EXISTS stock_moves(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      mdate TEXT NOT NULL,
      product TEXT NOT NULL,
      qty REAL NOT NULL,  -- + giriş, - çıkış
      unit TEXT NOT NULL,
      note TEXT
    )""")

    # Özbağ HAS cari (tek satır net bakiye: + Özbağ size borçlu, - siz Özbağ'a)
    run("""
    CREATE TABLE IF NOT EXISTS ozbag_balance(
      id INTEGER PRIMARY KEY CHECK(id=1),
      has_net REAL NOT NULL
    )""")
    if qdf("SELECT COUNT(*) n FROM ozbag_balance").iloc[0,0] == 0:
        run("INSERT INTO ozbag_balance(id,has_net) VALUES(1,0.0)")

    # Özbağ – bilezik giriş kayıtları (milyem bazında)
    run("""
    CREATE TABLE IF NOT EXISTS ozbag_bracelet_entries(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      bdate TEXT NOT NULL,
      name TEXT NOT NULL,       -- bilezik türü/ismi
      mil REAL NOT NULL,        -- 916, 917 ...
      gram REAL NOT NULL,
      has_rate REAL NOT NULL,   -- ₺/HAS (manuel)
      workmanship_tl REAL NOT NULL,  -- işçilik TL (toplam)
      has_equiv REAL NOT NULL,  -- gram * mil/1000
      total_tl REAL NOT NULL,   -- has_equiv * has_rate + workmanship_tl
      note TEXT
    )""")

    # Varsayılan bankalar ve açılışları
    defaults = [
        ("Vakıfbank",      0.0, 2.8, 1),
        ("İş Bankası",     0.0, 3.6, 1),
        ("Ziraat Bankası", 0.0, 3.6, 1),
        ("QNB Finansbank", 0.0, 3.6, 1),
    ]
    for name, sale_pct, adv_pct, d in defaults:
        if qdf("SELECT COUNT(*) n FROM banks WHERE name=?", (name,)).iloc[0,0] == 0:
            run("INSERT INTO banks(name,pos_sale_pct,cash_adv_pct,settle_days) VALUES(?,?,?,?)",
                (name, sale_pct, adv_pct, d))
        if qdf("SELECT COUNT(*) n FROM bank_openings WHERE bank=?", (name,)).iloc[0,0] == 0:
            run("INSERT INTO bank_openings(bank,balance_tl) VALUES(?,?)", (name, 0.0))

    # Ürün kataloğu (sabit)
    base_products = [
        ("Çeyrek Altın",    "adet"),
        ("Yarım Altın",     "adet"),
        ("Tam Altın",       "adet"),
        ("Ata Lira",        "adet"),
        ("24 Ayar Gram",    "gr"),
        ("22 Ayar Gram",    "gr"),
        ("22 Ayar 0,5 gr",  "adet"),
        ("22 Ayar 0,25 gr", "adet"),
        ("Bilezik 22K",     "gr"),   # stok gram olarak tutulur, maliyet/milyem girişleri Özbağ panelinde
    ]
    for n,u in base_products:
        if qdf("SELECT COUNT(*) n FROM products WHERE name=?", (n,)).iloc[0,0] == 0:
            run("INSERT INTO products(name,unit) VALUES(?,?)", (n,u))

ensure_schema()


# ---------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------
def banks_df() -> pd.DataFrame:
    return qdf("SELECT * FROM banks ORDER BY name")

def bank_openings_df() -> pd.DataFrame:
    return qdf("SELECT * FROM bank_openings ORDER BY bank")

def products_df() -> pd.DataFrame:
    return qdf("SELECT name,unit FROM products ORDER BY name")

def cash_opening() -> float:
    return float(qdf("SELECT cash_tl FROM openings WHERE id=1").iloc[0,0])

def set_cash_open(val: float):
    run("UPDATE openings SET cash_tl=? WHERE id=1", (float(val),))

def set_bank_open(bank: str, bal: float):
    run("UPDATE bank_openings SET balance_tl=? WHERE bank=?", (float(bal), bank))

def update_bank(name: str, sale_pct: float, adv_pct: float, days: int):
    run("UPDATE banks SET pos_sale_pct=?, cash_adv_pct=?, settle_days=? WHERE name=?",
        (sale_pct, adv_pct, days, name))

def add_stock(product: str, qty: float, unit: str, note: str):
    run("""INSERT INTO stock_moves(ts,mdate,product,qty,unit,note)
           VALUES(?,?,?,?,?,?)""", (NOW, date.today().isoformat(), product, qty, unit, note))

def stock_summary() -> pd.DataFrame:
    df = qdf("""SELECT product, unit, SUM(qty) AS qty
                FROM stock_moves GROUP BY product, unit ORDER BY product""")
    return df

def add_sale_header(customer: str, note: str) -> int:
    sdate = date.today().isoformat()
    run("INSERT INTO sales(ts,sdate,customer,note) VALUES(?,?,?,?)", (NOW, sdate, customer, note))
    return int(qdf("SELECT last_insert_rowid() AS id").iloc[0,0])

def add_sale_item(sale_id: int, product: str, qty: float, unit: str, unit_price: float):
    line_total = round(qty * unit_price, 2)
    run("""INSERT INTO sale_items(sale_id,product,qty,unit,unit_price,line_total)
           VALUES(?,?,?,?,?,?)""", (sale_id, product, qty, unit, unit_price, line_total))

def add_payment(method: str, direction: str, gross: float,
                bank: Optional[str]=None, fee_pct: float=0.0, settle_days: int=0,
                note: str="", sale_id: Optional[int]=None):
    fee_amount = round(gross * fee_pct/100.0, 2)
    net_settle = round(gross - fee_amount, 2)
    pdate = date.today().isoformat()
    settle = (date.today() + timedelta(days=settle_days)).isoformat() if bank else None
    run("""INSERT INTO payments(ts,pdate,sale_id,method,bank,gross_amount,fee_pct,fee_amount,net_settlement,settle_date,direction,note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (NOW, pdate, sale_id, method, bank, gross, fee_pct, fee_amount, net_settle, settle, direction, note))

def add_transfer(ttype: str, bank: str, amount: float, note: str):
    run("INSERT INTO transfers(ts,tdate,ttype,bank,amount,note) VALUES(?,?,?,?,?,?)",
        (NOW, date.today().isoformat(), ttype, bank, amount, note))

def ozbag_net() -> float:
    return float(qdf("SELECT has_net FROM ozbag_balance WHERE id=1").iloc[0,0])

def set_ozbag_net(val: float):
    run("UPDATE ozbag_balance SET has_net=? WHERE id=1", (float(val),))

def add_ozbag_bracelet(name: str, mil: float, gram: float, has_rate: float,
                       workmanship_tl: float, note: str, add_to_ozbag_cari: bool):
    # HAS eşdeğer ve toplam TL
    has_equiv = round(gram * (mil/1000.0), 3)
    total_tl = round(has_equiv * has_rate + workmanship_tl, 2)
    bdate = date.today().isoformat()
    run("""INSERT INTO ozbag_bracelet_entries(ts,bdate,name,mil,gram,has_rate,workmanship_tl,has_equiv,total_tl,note)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (NOW, bdate, name, mil, gram, has_rate, workmanship_tl, has_equiv, total_tl, note))
    # Stok: Bilezik 22K gram artar
    add_stock("Bilezik 22K", gram, "gr", f"Özbağ bilezik girişi ({name} {mil}‰)")
    # Özbağ cari: has bazında borç (+) (Özbağ'a borçlanıyoruz)
    if add_to_ozbag_cari:
        cur = ozbag_net()
        set_ozbag_net(cur - has_equiv)  # net<0: Özbağ'a borcuz
    return has_equiv, total_tl


# ---------------------------------------------------------
# Bakiye Motoru
# ---------------------------------------------------------
def cash_balance() -> float:
    # Açılış
    bal = cash_opening()

    # Nakit ödeme bacakları
    df_cash = qdf("""SELECT direction, gross_amount FROM payments WHERE method='CASH'""")
    for _, r in df_cash.iterrows():
        a = float(r["gross_amount"])
        bal += a if r["direction"] == "INFLOW" else -a

    # Transferler
    df_tr = qdf("SELECT ttype, amount FROM transfers")
    for _, r in df_tr.iterrows():
        a = float(r["amount"])
        if r["ttype"] == "BANK_TO_CASH":
            bal += a
        else:
            bal -= a
    return round(bal, 2)

def bank_balances(include_pending: bool=False) -> Dict[str, float]:
    # Açılışlar
    base = bank_openings_df().set_index("bank")["balance_tl"].astype(float).to_dict()

    # POS netleri (CARD / CASH_ADV) -> settle_date geldiğinde eklenir
    if include_pending:
        df = qdf("""SELECT bank, net_settlement FROM payments
                    WHERE bank IS NOT NULL AND method IN ('CARD','CASH_ADV')""")
    else:
        today = date.today().isoformat()
        df = qdf("""SELECT bank, net_settlement FROM payments
                    WHERE bank IS NOT NULL AND method IN ('CARD','CASH_ADV')
                      AND settle_date <= ?""", (today,))
    for _, r in df.iterrows():
        b = r["bank"]; n = float(r["net_settlement"])
        base[b] = base.get(b, 0.0) + n

    # Transferler
    tr = qdf("SELECT bank, ttype, amount FROM transfers")
    for _, r in tr.iterrows():
        b = r["bank"]; a = float(r["amount"])
        if r["ttype"] == "CASH_TO_BANK":
            base[b] = base.get(b, 0.0) + a
        else:
            base[b] = base.get(b, 0.0) - a
    return {k: round(v, 2) for k, v in base.items()}

def today_settlements() -> pd.DataFrame:
    today = date.today().isoformat()
    return qdf("""SELECT pdate, bank, method, gross_amount, fee_pct, fee_amount, net_settlement, settle_date, note
                  FROM payments
                  WHERE bank IS NOT NULL AND settle_date = ?
                  ORDER BY bank, pdate""", (today,))

def pending_settlements() -> pd.DataFrame:
    today = date.today().isoformat()
    return qdf("""SELECT pdate, bank, method, gross_amount, fee_pct, fee_amount, net_settlement, settle_date, note
                  FROM payments
                  WHERE bank IS NOT NULL AND settle_date > ?
                  ORDER BY settle_date, bank""", (today,))


# ---------------------------------------------------------
# UI
# ---------------------------------------------------------
st.title("💎 Sarıkaya Kuyumculuk — Satış / POS / Özbağ (Milyemli Bilezik)")
tabs = st.tabs([
    "⚙️ Ayarlar & Açılış",
    "🛒 Normal Satış",
    "🧾 Parçalı Ödeme (Satış)",
    "💳 Kart→Nakit (Tek Geçiş)",
    "🟡 Özbağ — Bilezik Girişi (Milyem)",
    "🔁 Kasa ⇄ Banka Transfer",
    "📦 Stok & Özbağ Cari",
    "📊 Rapor & Ekstre"
])

# ----- 1) Settings -----
with tabs[0]:
    st.subheader("Açılış Bakiyeleri")
    col1, col2 = st.columns([1,2])
    with col1:
        cash_open = st.number_input("Kasa Açılış (₺)", min_value=0.0, step=100.0, value=float(cash_opening()), key="open_cash")
        if st.button("Kasa Açılışı Kaydet", key="btn_open_cash"):
            set_cash_open(cash_open); st.success("Kasa açılışı güncellendi.")
    with col2:
        st.markdown("**Banka Açılışları (₺)**")
        bdf = bank_openings_df()
        for i, row in bdf.iterrows():
            name = row["bank"]
            val = st.number_input(f"{name}", min_value=0.0, value=float(row["balance_tl"]), step=100.0, key=f"bo_{name}")
            if st.button(f"{name} güncelle", key=f"bo_btn_{name}"):
                set_bank_open(name, val); st.success(f"{name} açılışı güncellendi.")

    st.divider()
    st.subheader("Banka POS Ayarları")
    st.caption("Satış POS komisyonu ve **Kart→Nakit** tek geçiş komisyonu; yatış süresi (gün).")
    b = banks_df()
    for _, r in b.iterrows():
        n = r["name"]
        cols = st.columns([2,1,1,1])
        cols[0].markdown(f"**{n}**")
        sale_pct = cols[1].number_input("Satış POS %", min_value=0.0, value=float(r["pos_sale_pct"]), step=0.1, key=f"fs_{n}")
        cashadv_pct = cols[2].number_input("Kart→Nakit %", min_value=0.0, value=float(r["cash_adv_pct"]), step=0.1, key=f"fc_{n}")
        days = cols[3].number_input("Yatış (gün)", min_value=0, value=int(r["settle_days"]), step=1, key=f"sd_{n}")
        if st.button(f"{n} kaydet", key=f"bank_save_{n}"):
            update_bank(n, sale_pct, cashadv_pct, days)
            st.success(f"{n} ayarları güncellendi.")

# ----- 2) Normal Satış (tek form; çoklu kalem ekleyebilirsin) -----
with tabs[1]:
    st.subheader("🛒 Normal Satış (Ürün Seçerek)")
    prods = products_df()
    if "sale_lines" not in st.session_state:
        st.session_state["sale_lines"] = []  # {product, qty, unit, unit_price}

    sc = st.columns([2,1,2])
    with sc[0]:
        s_customer = st.text_input("Müşteri (ops.)", key="ns_cust")
    with sc[1]:
        s_note = st.text_input("Not", key="ns_note")
    with sc[2]:
        st.caption("Önce kalemleri ekle, sonra ödemeyi Parçalı Ödeme sekmesinde yapabilirsin.")

    lc = st.columns([3,1,1,1,1])
    with lc[0]:
        p_sel = st.selectbox("Ürün", list(prods["name"]), key="ns_prod")
        p_unit = prods[prods["name"]==p_sel].iloc[0]["unit"]
    with lc[1]:
        qty = st.number_input("Miktar", min_value=0.0, step=1.0, key="ns_qty")
    with lc[2]:
        st.text_input("Birim", value=p_unit, disabled=True, key="ns_unit_ro")
    with lc[3]:
        unit_price = st.number_input("Birim Fiyat (₺)", min_value=0.0, step=10.0, key="ns_uprice")
    with lc[4]:
        if st.button("Kalem Ekle", key="ns_add_line"):
            st.session_state["sale_lines"].append({
                "product": p_sel, "qty": qty, "unit": p_unit, "unit_price": unit_price
            })

    if st.session_state["sale_lines"]:
        df_lines = pd.DataFrame(st.session_state["sale_lines"])
        df_lines["line_total"] = (df_lines["qty"] * df_lines["unit_price"]).round(2)
        st.dataframe(df_lines, use_container_width=True)
        tot = float(df_lines["line_total"].sum())
        st.metric("Satış Toplam", f"{tot:,.2f} ₺")
        if st.button("Satışı Kaydet (Sadece Kalemler)", key="ns_save_sale"):
            sid = add_sale_header(s_customer, s_note)
            for r in st.session_state["sale_lines"]:
                add_sale_item(sid, r["product"], float(r["qty"]), r["unit"], float(r["unit_price"]))
                # stok düş: satılan miktar kadar eksi
                add_stock(r["product"], -float(r["qty"]), r["unit"], f"Satış #{sid}")
            st.success(f"Satış kalemleri kaydedildi (#{sid}). Ödemeleri 'Parçalı Ödeme' sekmesinden girin.")
            st.session_state["sale_lines"] = []
    else:
        st.info("Kalem eklenmedi.")

# ----- 3) Parçalı Ödeme (satış toplamını karşılayan bacaklar) -----
with tabs[2]:
    st.subheader("🧾 Parçalı Ödeme")
    # Son satışları göster ve birini seç
    ss = qdf("""SELECT s.id, s.sdate, IFNULL(s.customer,'-') AS customer,
                       SUM(i.line_total) AS total
                FROM sales s
                JOIN sale_items i ON i.sale_id = s.id
                GROUP BY s.id, s.sdate, s.customer
                ORDER BY s.id DESC LIMIT 30""")
    if ss.empty:
        st.info("Kayıtlı satış yok. Önce 'Normal Satış' sekmesinden satış kalemlerini girin.")
    else:
        st.dataframe(ss, use_container_width=True)
        sid_opts = list(ss["id"])
        s_sel = st.selectbox("Satış Seç (ID)", sid_opts, key="po_sid")
        sel_total = float(ss[ss["id"]==s_sel].iloc[0]["total"])
        st.metric("Seçili Satış Toplamı", f"{sel_total:,.2f} ₺")

        if "po_legs" not in st.session_state:
            st.session_state["po_legs"] = []

        leg_cols = st.columns([2,2,2,2,2])
        leg_method = leg_cols[0].selectbox("Yöntem", ["NAKIT","HAVALE","KART"], key="po_leg_method")
        leg_amt = leg_cols[1].number_input("Tutar (₺)", min_value=0.0, step=10.0, key="po_leg_amt")
        leg_bank = None; fee_pct = 0.0; settle_days = 0
        if leg_method == "KART":
            bdf = banks_df()
            leg_bank = leg_cols[2].selectbox("Banka", list(bdf["name"]), key="po_leg_bank")
            fee_pct = float(bdf[bdf["name"]==leg_bank].iloc[0]["pos_sale_pct"])
            settle_days = int(bdf[bdf["name"]==leg_bank].iloc[0]["settle_days"])
            leg_cols[3].number_input("Komisyon %", min_value=0.0, value=fee_pct, step=0.1, key="po_leg_fee_ro", disabled=True)
            leg_cols[4].number_input("Yatış (gün)", min_value=0, value=settle_days, step=1, key="po_leg_settle_ro", disabled=True)
        else:
            leg_cols[2].text_input("Banka", value="-", key="po_leg_bank_dummy", disabled=True)
            leg_cols[3].number_input("Komisyon %", min_value=0.0, value=0.0, step=0.1, key="po_leg_fee_dummy", disabled=True)
            leg_cols[4].number_input("Yatış (gün)", min_value=0, value=0, step=1, key="po_leg_settle_dummy", disabled=True)

        if st.button("Bacak Ekle", key="po_add_leg"):
            st.session_state["po_legs"].append({
                "method": leg_method, "amount": leg_amt, "bank": leg_bank
            })

        if st.session_state["po_legs"]:
            st.dataframe(pd.DataFrame(st.session_state["po_legs"]), use_container_width=True)
            legs_sum = sum(x["amount"] for x in st.session_state["po_legs"])
            st.metric("Bacak Toplamı", f"{legs_sum:,.2f} ₺")
        else:
            st.info("Henüz bacak eklenmedi.")

        if st.button("Ödemeleri Kaydet", key="po_save"):
            legs_sum = sum(x["amount"] for x in st.session_state["po_legs"])
            if legs_sum < sel_total - 0.01:
                st.warning("Bacak toplamı satış toplamını karşılamıyor.")
            bdf = banks_df()
            for leg in st.session_state["po_legs"]:
                m = leg["method"]; amt = float(leg["amount"])
                if m == "NAKIT":
                    add_payment("CASH", "INFLOW", amt, note=f"Sale #{s_sel}", sale_id=s_sel)
                elif m == "HAVALE":
                    add_payment("TRANSFER", "INFLOW", amt, note=f"Sale #{s_sel} (havale)", sale_id=s_sel)
                else:
                    bname = leg["bank"]
                    row = bdf[bdf["name"]==bname].iloc[0]
                    fee_pct = float(row["pos_sale_pct"]); delay = int(row["settle_days"])
                    add_payment("CARD", "INFLOW", amt, bank=bname, fee_pct=fee_pct, settle_days=delay,
                                note=f"Sale #{s_sel} (kart)", sale_id=s_sel)
            st.success("Ödemeler kaydedildi.")
            st.session_state["po_legs"] = []

# ----- 4) Kart → Nakit (tek geçiş) -----
with tabs[3]:
    st.subheader("💳 Karttan Çekip Nakit Verme (Tek Geçiş)")
    bdf = banks_df()
    colA, colB, colC = st.columns(3)
    with colA:
        adv_bank = st.selectbox("Banka", list(bdf["name"]), key="adv_bank2")
    with colB:
        cash_given = st.number_input("Verilen Nakit (₺)", min_value=0.0, step=50.0, key="adv_cash2")
    with colC:
        surcharge_pct = st.number_input("Müşteriye yansıttığın %", min_value=0.0, value=8.0, step=0.5, key="adv_surcharge2")

    gross_charge = round(cash_given * (1 + surcharge_pct/100.0), 2)
    row = bdf[bdf["name"]==adv_bank].iloc[0]
    fee_pct = float(row["cash_adv_pct"])
    settle_days = int(row["settle_days"])
    fee_amt = round(gross_charge * fee_pct / 100.0, 2)
    net_settle = round(gross_charge - fee_amt, 2)
    profit = round(gross_charge - fee_amt - cash_given, 2)

    st.markdown("#### Hesap")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Karttan Geçen (Brüt)", f"{gross_charge:,.2f} ₺")
    m2.metric(f"Banka Kesintisi ({fee_pct:.1f}%)", f"{fee_amt:,.2f} ₺")
    m3.metric("Ertesi Gün Yatan (Net)", f"{net_settle:,.2f} ₺")
    m4.metric("Anlık Kâr", f"{profit:,.2f} ₺")

    note_adv = st.text_input("Not", key="adv_note2")
    if st.button("Kaydet (Kart→Nakit)", key="btn_save_adv2"):
        # 1) POS brüt tahsilat (INFLOW), bankaya yarın net yatacak
        add_payment("CASH_ADV", "INFLOW", gross_charge, bank=adv_bank, fee_pct=fee_pct,
                    settle_days=settle_days, note=f"CashAdv: {note_adv}")
        # 2) Nakit çıkışı (kasadan müşteriye)
        add_payment("CASH", "OUTFLOW", cash_given, note=f"CashAdv payout: {note_adv}")
        st.success("Kart→Nakit işlemi kaydedildi.")

# ----- 5) Özbağ — Bilezik Girişi (milyem) -----
with tabs[4]:
    st.subheader("🟡 Özbağ — Bilezik Girişi (Milyem Bazında)")
    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        blz_name = st.text_input("Bilezik Adı (ör. Trabzon/Burma...)", key="bz_name")
        mil = st.number_input("Milyem (‰)", min_value=800.0, value=916.0, step=0.5, key="bz_mil")
    with bc2:
        gram = st.number_input("Gram", min_value=0.0, step=0.10, key="bz_gram")
        has_rate = st.number_input("HAS Kuru (₺/HAS)", min_value=0.0, step=1.0, key="bz_has_rate")
    with bc3:
        workmanship = st.number_input("İşçilik (TL)", min_value=0.0, step=10.0, key="bz_work")
        add_cari = st.checkbox("Özbağ cari borca işle (HAS)", value=True, key="bz_cari_chk")
    bz_note = st.text_input("Not", key="bz_note")

    if st.button("Girişi Kaydet", key="bz_save"):
        has_eq, total_tl = add_ozbag_bracelet(
            name=blz_name or "Bilezik",
            mil=float(mil),
            gram=float(gram),
            has_rate=float(has_rate),
            workmanship_tl=float(workmanship),
            note=bz_note,
            add_to_ozbag_cari=bool(add_cari)
        )
        st.success(f"Girdi: {gram:.2f} gr, {mil:.1f}‰ → {has_eq:.3f} HAS • Toplam {total_tl:,.2f} ₺")

    st.markdown("##### Son 20 Bilezik Girişi")
    st.dataframe(qdf("""SELECT bdate, name, mil, gram, has_rate, workmanship_tl, has_equiv, total_tl, note
                        FROM ozbag_bracelet_entries
                        ORDER BY id DESC LIMIT 20"""),
                 use_container_width=True)

# ----- 6) Transfers -----
with tabs[5]:
    st.subheader("🔁 Kasa ⇄ Banka Transferleri")
    ttype = st.selectbox("Tür", ["KASA → BANKA", "BANKA → KASA"], key="trf_type2")
    bname = st.selectbox("Banka", list(banks_df()["name"]), key="trf_bank2")
    amt = st.number_input("Tutar (₺)", min_value=0.0, step=50.0, key="trf_amt2")
    note = st.text_input("Not", key="trf_note2")
    if st.button("Transferi Kaydet", key="btn_trf2"):
        if ttype.startswith("KASA"):
            add_transfer("CASH_TO_BANK", bname, amt, note)
        else:
            add_transfer("BANK_TO_CASH", bname, amt, note)
        st.success("Transfer kaydedildi.")

    st.markdown("#### Son Transferler")
    st.dataframe(qdf("SELECT tdate, ttype, bank, amount, note FROM transfers ORDER BY id DESC LIMIT 30"),
                 use_container_width=True)

# ----- 7) Stok & Özbağ Cari -----
with tabs[6]:
    st.subheader("📦 Stok Özeti")
    df_stock = stock_summary()
    if df_stock.empty:
        st.info("Stok hareketi yok.")
    else:
        st.dataframe(df_stock, use_container_width=True)

    st.markdown("#### Özbağ Cari (HAS bazında)")
    net = ozbag_net()
    st.metric("Özbağ Net", f"{net:,.3f} HAS", help="+: Özbağ size borçlu, −: Özbağ’a borcunuz")
    if st.checkbox("Manuel düzeltme (HAS)", key="oz_fix_chk"):
        newv = st.number_input("Yeni net (HAS)", value=float(net), step=0.1, key="oz_fix_val")
        if st.button("Güncelle", key="oz_fix_btn"):
            set_ozbag_net(newv); st.success("Özbağ cari güncellendi.")

# ----- 8) Raporlar -----
with tabs[7]:
    st.subheader("📊 Kasa / Banka / POS Ekstre")
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Kasa Bakiye (₺)", f"{cash_balance():,.2f} ₺")
    with c2:
        st.markdown("**Banka Bakiyeleri (Yatanlar Dahil)**")
        bs = bank_balances(include_pending=False)
        if not bs:
            st.info("Banka yok.")
        else:
            for k, v in bs.items():
                st.metric(k, f"{v:,.2f} ₺")

    st.divider()
    st.markdown("### Bugün Hesaba Geçecek POS (Net)")
    td = today_settlements()
    if td.empty:
        st.info("Bugün yatacak POS yok.")
    else:
        st.dataframe(td, use_container_width=True)
        st.metric("Toplam Net", f"{td['net_settlement'].sum():,.2f} ₺")

    st.markdown("### Bekleyen POS (Yarın ve sonrası)")
    pend = pending_settlements()
    if pend.empty:
        st.info("Bekleyen POS yok.")
    else:
        st.dataframe(pend, use_container_width=True)

    st.divider()
    st.markdown("### POS Komisyon Giderleri (Tarih Aralığı)")
    d1, d2 = st.columns(2)
    with d1:
        start = st.date_input("Başlangıç", value=date.today().replace(day=1), key="r_start2")
    with d2:
        end = st.date_input("Bitiş", value=date.today(), key="r_end2")
    rep = qdf("""SELECT pdate, bank, method, gross_amount, fee_pct, fee_amount
                 FROM payments
                 WHERE bank IS NOT NULL
                   AND pdate BETWEEN ? AND ?""", (start.isoformat(), end.isoformat()))
    if rep.empty:
        st.info("Kayıt yok.")
    else:
        st.dataframe(rep, use_container_width=True)
        st.metric("Toplam Komisyon", f"{rep['fee_amount'].sum():,.2f} ₺")

    st.markdown("### Kart→Nakit Kârlılık (Tarih Aralığı)")
    adv = qdf("""SELECT pdate, bank, gross_amount, fee_amount, net_settlement, note
                 FROM payments
                 WHERE method='CASH_ADV' AND pdate BETWEEN ? AND ?""",
                 (start.isoformat(), end.isoformat()))
    if adv.empty:
        st.info("Kart→Nakit kaydı yok.")
    else:
        # aynı gün içindeki CASH OUTFLOW toplamını yaklaşık ödeme kabul edelim
        out = qdf("""SELECT pdate, SUM(gross_amount) as cash_out
                     FROM payments
                     WHERE method='CASH' AND direction='OUTFLOW'
                       AND pdate BETWEEN ? AND ?
                     GROUP BY pdate""", (start.isoformat(), end.isoformat()))
        out_map = dict(zip(out["pdate"], out["cash_out"]))
        adv["payout_cash"] = adv["pdate"].map(out_map).fillna(0.0)
        adv["profit"] = adv["gross_amount"] - adv["fee_amount"] - adv["payout_cash"]
        st.dataframe(adv, use_container_width=True)
        st.metric("Toplam Kâr (yaklaşık)", f"{adv['profit'].sum():,.2f} ₺")