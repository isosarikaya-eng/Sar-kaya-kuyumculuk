import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Tuple

import pandas as pd
import streamlit as st

# ------------------- GENEL -------------------
st.set_page_config(page_title="Sarıkaya Kuyumculuk — Satış & POS", layout="wide")
DB_PATH = "sarikaya_pos.db"

# ------------------- DB HELPERS ---------------
def conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def run(sql: str, params: tuple = ()):
    with conn() as c:
        c.execute(sql, params)
        c.commit()

def qdf(sql: str, params: tuple = ()) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(sql, c, params=params)

# ------------------- SCHEMA -------------------
def ensure_schema():
    # bank accounts (settings)
    run("""
    CREATE TABLE IF NOT EXISTS banks (
      name TEXT PRIMARY KEY,
      fee_sale_pct REAL NOT NULL,
      fee_cash_adv_pct REAL NOT NULL,
      settle_days INTEGER NOT NULL
    )""")

    # opening balances
    run("""
    CREATE TABLE IF NOT EXISTS openings (
      id INTEGER PRIMARY KEY CHECK(id=1),
      cash_tl REAL NOT NULL DEFAULT 0
    )""")
    if qdf("SELECT COUNT(*) n FROM openings").iloc[0,0] == 0:
        run("INSERT INTO openings(id, cash_tl) VALUES(1,0)")

    run("""
    CREATE TABLE IF NOT EXISTS bank_openings (
      bank TEXT PRIMARY KEY,
      balance_tl REAL NOT NULL DEFAULT 0,
      FOREIGN KEY(bank) REFERENCES banks(name)
    )""")

    # sales (header) — optional; we mostly use payment legs
    run("""
    CREATE TABLE IF NOT EXISTS sales (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      sdate TEXT NOT NULL,
      customer TEXT,
      total_amount REAL NOT NULL,
      note TEXT
    )""")

    # payment legs for a sale (or standalone ops)
    # method: CASH / TRANSFER / CARD / CASH_ADV (card->cash)
    run("""
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      pdate TEXT NOT NULL,
      sale_id INTEGER,
      method TEXT NOT NULL,
      bank TEXT,                   -- for CARD or CASH_ADV
      gross_amount REAL NOT NULL,  -- tutar (kartta POS brüt)
      fee_pct REAL NOT NULL,
      fee_amount REAL NOT NULL,
      net_settlement REAL NOT NULL,
      settle_date TEXT,            -- when bank credits
      direction TEXT NOT NULL,     -- INFLOW / OUTFLOW
      note TEXT,
      FOREIGN KEY(sale_id) REFERENCES sales(id)
    )""")

    # cash-bank transfers
    # type: CASH_TO_BANK or BANK_TO_CASH
    run("""
    CREATE TABLE IF NOT EXISTS transfers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      tdate TEXT NOT NULL,
      ttype TEXT NOT NULL,
      bank TEXT,
      amount REAL NOT NULL,
      note TEXT
    )""")

    # POSTING: to compute balances quickly, we just query tables each time

    # default banks
    defaults = [
        ("Vakıfbank", 0.0, 2.8, 1),  # fee_sale set 0 by default, you can set; cash adv 2.8
        ("İş Bankası", 0.0, 3.6, 1),
        ("Ziraat Bankası", 0.0, 3.6, 1),
        ("QNB Finansbank", 0.0, 3.6, 1),
    ]
    for name, fs, fc, sd in defaults:
        if qdf("SELECT COUNT(*) n FROM banks WHERE name=?", (name,)).iloc[0,0] == 0:
            run("INSERT INTO banks(name, fee_sale_pct, fee_cash_adv_pct, settle_days) VALUES(?,?,?,?)",
                (name, fs, fc, sd))
        if qdf("SELECT COUNT(*) n FROM bank_openings WHERE bank=?", (name,)).iloc[0,0] == 0:
            run("INSERT INTO bank_openings(bank, balance_tl) VALUES(?,?)", (name, 0.0))

ensure_schema()

# ------------------- HELPERS -------------------
def banks_df() -> pd.DataFrame:
    return qdf("SELECT * FROM banks ORDER BY name")

def bank_openings_df() -> pd.DataFrame:
    return qdf("SELECT * FROM bank_openings ORDER BY bank")

def get_cash_opening() -> float:
    return float(qdf("SELECT cash_tl FROM openings WHERE id=1").iloc[0,0])

def set_cash_opening(val: float):
    run("UPDATE openings SET cash_tl=? WHERE id=1", (float(val),))

def update_bank_fee(name: str, sale_pct: float, cash_adv_pct: float, days: int):
    run("UPDATE banks SET fee_sale_pct=?, fee_cash_adv_pct=?, settle_days=? WHERE name=?",
        (sale_pct, cash_adv_pct, days, name))

def set_bank_opening(name: str, bal: float):
    run("UPDATE bank_openings SET balance_tl=? WHERE bank=?", (float(bal), name))

def add_sale(total_amount: float, customer: str, note: str) -> int:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = date.today().isoformat()
    run("INSERT INTO sales(ts,sdate,customer,total_amount,note) VALUES(?,?,?,?,?)",
        (ts, today, customer, total_amount, note))
    sid = int(qdf("SELECT last_insert_rowid() as id").iloc[0,0])
    return sid

def add_payment(method: str, bank: Optional[str], gross: float,
                fee_pct: float, direction: str, note: str,
                sale_id: Optional[int] = None, settle_days: int = 1):
    # fee & settlement
    fee_amt = round(gross * fee_pct / 100.0, 2)
    net = round(gross - fee_amt, 2)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdate = date.today().isoformat()
    settle_date = (date.today() + timedelta(days=settle_days)).isoformat() if bank else None
    run("""INSERT INTO payments(ts,pdate,sale_id,method,bank,gross_amount,fee_pct,fee_amount,net_settlement,settle_date,direction,note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, pdate, sale_id, method, bank, gross, fee_pct, fee_amt, net, settle_date, direction, note))

def add_transfer(ttype: str, bank: str, amount: float, note: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tdate = date.today().isoformat()
    run("""INSERT INTO transfers(ts,tdate,ttype,bank,amount,note) VALUES(?,?,?,?,?,?)""",
        (ts, tdate, ttype, bank, amount, note))

# ------------------- BALANCE ENGINE -------------------
def cash_balance() -> float:
    # opening cash
    opening = get_cash_opening()
    # cash legs of payments:
    # - SALE cash leg: INFLOW, method=CASH, no fee
    # - TRANSFER cash leg: INFLOW (Havale), but that's bank; not cash.
    # transactions affecting cash:
    # 1) payments where method=CASH and direction=INFLOW -> +
    # 2) payments where method=CASH and direction=OUTFLOW -> -
    # 3) CASH_ADV: immediate cash OUTFLOW equals given cash to customer? We model as separate OUTFLOW leg
    #    Here we only record CARD gross to bank; cash given recorded as CASH OUTFLOW leg when creating cash-advance.
    df = qdf("""SELECT method, direction, gross_amount FROM payments
                WHERE method='CASH'""")
    flow = 0.0
    for _, r in df.iterrows():
        amt = float(r["gross_amount"])
        if r["direction"] == "INFLOW":
            flow += amt
        else:
            flow -= amt
    # transfers: BANK_TO_CASH increases; CASH_TO_BANK decreases
    tr = qdf("SELECT ttype, amount FROM transfers")
    for _, r in tr.iterrows():
        a = float(r["amount"])
        if r["ttype"] == "BANK_TO_CASH": flow += a
        else: flow -= a
    return round(opening + flow, 2)

def bank_balances(include_pending: bool = False) -> Dict[str, float]:
    # Start from opening per bank
    openings = bank_openings_df().set_index("bank")["balance_tl"].astype(float).to_dict()
    balances = {k: float(v) for k, v in openings.items()}

    # Settled POS inflows (CARD or CASH_ADV) -> net_settlement on settle_date (t<=today if not include_pending)
    if include_pending:
        df = qdf("""SELECT bank, net_settlement FROM payments
                    WHERE bank IS NOT NULL AND (method='CARD' OR method='CASH_ADV')""")
    else:
        today = date.today().isoformat()
        df = qdf("""SELECT bank, net_settlement FROM payments
                    WHERE bank IS NOT NULL AND (method='CARD' OR method='CASH_ADV')
                    AND settle_date <= ?""", (today,))
    for _, r in df.iterrows():
        balances[r["bank"]] = balances.get(r["bank"], 0.0) + float(r["net_settlement"])

    # transfers: CASH_TO_BANK increases bank; BANK_TO_CASH decreases bank
    tr = qdf("SELECT bank, ttype, amount FROM transfers")
    for _, r in tr.iterrows():
        a = float(r["amount"])
        b = r["bank"]
        if r["ttype"] == "CASH_TO_BANK":
            balances[b] = balances.get(b, 0.0) + a
        else:
            balances[b] = balances.get(b, 0.0) - a
    return {k: round(v, 2) for k, v in balances.items()}

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

# ------------------- UI -------------------
st.title("💎 Sarıkaya Kuyumculuk — Satış / POS / Kasa")
tabs = st.tabs([
    "⚙️ Ayarlar & Açılış",
    "🧾 Satış (Parçalı Ödeme)",
    "💳 Kart→Nakit (Tek Geçiş)",
    "🔁 Kasa ⇄ Banka Transfer",
    "📊 Rapor & Ekstre"
])

# ----- 1) Settings -----
with tabs[0]:
    st.subheader("Açılış Bakiyeleri")
    col1, col2 = st.columns([1,2])
    with col1:
        cash_open = st.number_input("Kasa Açılış (₺)", min_value=0.0, step=100.0, key="open_cash")
        if st.button("Kasa Açılışı Kaydet", key="btn_open_cash"):
            set_cash_opening(cash_open); st.success("Kasa açılışı güncellendi.")
    with col2:
        st.markdown("**Banka Açılışları (₺)**")
        bdf = bank_openings_df()
        for i, row in bdf.iterrows():
            name = row["bank"]
            val = st.number_input(f"{name}", min_value=0.0, value=float(row["balance_tl"]), step=100.0, key=f"bo_{name}")
            if st.button(f"{name} güncelle", key=f"bo_btn_{name}"):
                set_bank_opening(name, val); st.success(f"{name} açılışı güncellendi.")

    st.divider()
    st.subheader("Banka POS Ayarları")
    st.caption("Satış POS komisyonu (genelde mağaza satışı) ve **Kart→Nakit** tek geçiş komisyonu.")
    b = banks_df()
    for _, r in b.iterrows():
        n = r["name"]
        cols = st.columns([2,1,1,1])
        cols[0].markdown(f"**{n}**")
        sale_pct = cols[1].number_input("Satış POS %", min_value=0.0, value=float(r["fee_sale_pct"]), step=0.1, key=f"fs_{n}")
        cashadv_pct = cols[2].number_input("Kart→Nakit %", min_value=0.0, value=float(r["fee_cash_adv_pct"]), step=0.1, key=f"fc_{n}")
        days = cols[3].number_input("Yatış (gün)", min_value=0, value=int(r["settle_days"]), step=1, key=f"sd_{n}")
        if st.button(f"{n} kaydet", key=f"bank_save_{n}"):
            update_bank_fee(n, sale_pct, cashadv_pct, days)
            st.success(f"{n} ayarları güncellendi.")

# ----- 2) Sale with split payments -----
with tabs[1]:
    st.subheader("Satış (Parçalı Ödeme)")
    scols = st.columns([2,1,2])
    with scols[0]:
        s_customer = st.text_input("Müşteri (ops.)", key="s_cust")
    with scols[1]:
        s_total = st.number_input("Satış Toplam (₺)", min_value=0.0, step=50.0, key="s_total")
    with scols[2]:
        s_note = st.text_input("Not", key="s_note")

    st.markdown("#### Ödeme Bacakları")
    if "legs" not in st.session_state:
        st.session_state["legs"] = []  # each leg: dict

    # Add leg UI
    leg_cols = st.columns([2,2,2,2,2])
    leg_method = leg_cols[0].selectbox("Yöntem", ["NAKIT","HAVALE","KART"], key="leg_method_new")
    leg_amt = leg_cols[1].number_input("Tutar (₺)", min_value=0.0, step=10.0, key="leg_amt_new")
    leg_bank = None
    fee_pct = 0.0
    settle_days = 1
    if leg_method == "KART":
        banks = banks_df()
        leg_bank = leg_cols[2].selectbox("Banka", list(banks["name"]), key="leg_bank_new")
        # POS komisyonu bu bankanın satış komisyonu
        fee_pct = float(banks[banks["name"]==leg_bank].iloc[0]["fee_sale_pct"])
        settle_days = int(banks[banks["name"]==leg_bank].iloc[0]["settle_days"])
        leg_cols[3].number_input("POS Komisyon %", min_value=0.0, value=fee_pct, step=0.1, key="leg_fee_view", disabled=True)
        leg_cols[4].number_input("Yatış (gün)", min_value=0, value=settle_days, step=1, key="leg_settle_view", disabled=True)
    else:
        leg_cols[2].text_input("Banka", value="-", key="leg_bank_dummy", disabled=True)
        leg_cols[3].number_input("Komisyon %", min_value=0.0, value=0.0, step=0.1, key="leg_fee_dummy", disabled=True)
        leg_cols[4].number_input("Yatış (gün)", min_value=0, value=0, step=1, key="leg_settle_dummy", disabled=True)

    if st.button("Bacak Ekle", key="btn_add_leg"):
        st.session_state["legs"].append({
            "method": leg_method,
            "amount": leg_amt,
            "bank": leg_bank if leg_method=="KART" else None,
        })

    # List legs
    if st.session_state["legs"]:
        leg_df = pd.DataFrame(st.session_state["legs"])
        st.dataframe(leg_df, use_container_width=True)
    else:
        st.info("Henüz bacak eklenmedi.")

    # Save sale
    if st.button("Satışı Kaydet", key="btn_save_sale"):
        if s_total <= 0:
            st.error("Satış toplamı > 0 olmalı.")
        elif not st.session_state["legs"]:
            st.error("En az 1 ödeme bacağı ekleyin.")
        else:
            legs_sum = sum(x["amount"] for x in st.session_state["legs"])
            if abs(legs_sum - s_total) > 0.01:
                st.warning(f"Ödeme bacakları toplamı ({legs_sum:.2f}) satış toplamına ({s_total:.2f}) eşit değil.")
            sid = add_sale(s_total, s_customer, s_note)
            # persist legs as payments
            bdf = banks_df()
            for leg in st.session_state["legs"]:
                m = leg["method"]
                amt = float(leg["amount"])
                if m == "NAKIT":
                    add_payment("CASH", None, amt, 0.0, "INFLOW", f"Sale #{sid}", sale_id=sid)
                elif m == "HAVALE":
                    # Havale doğrudan bankaya girmez; burada netleştirmiyoruz.
                    # İstersen “Havale=İş Bankası” gibi seçim ekleyebiliriz. Şimdilik bilgi amaçlı INFLOW (not).
                    add_payment("TRANSFER", None, amt, 0.0, "INFLOW", f"Sale #{sid} (havale)", sale_id=sid)
                else:  # KART
                    bname = leg["bank"]
                    row = bdf[bdf["name"]==bname].iloc[0]
                    fee_pct = float(row["fee_sale_pct"])
                    delay = int(row["settle_days"])
                    add_payment("CARD", bname, amt, fee_pct, "INFLOW", f"Sale #{sid} (kart)", sale_id=sid, settle_days=delay)
            st.success(f"Satış kaydedildi (#{sid}).")
            st.session_state["legs"] = []

# ----- 3) Card → Cash advance -----
with tabs[2]:
    st.subheader("Karttan Çekip Nakit Verme (Tek Geçiş)")
    colA, colB, colC = st.columns(3)
    banks = banks_df()
    with colA:
        adv_bank = st.selectbox("Banka", list(banks["name"]), key="adv_bank")
    with colB:
        cash_given = st.number_input("Verilen Nakit (₺)", min_value=0.0, step=50.0, key="adv_cash_given")
    with colC:
        surcharge_pct = st.number_input("Müşteriye yansıttığın %", min_value=0.0, value=8.0, step=0.5, key="adv_surcharge")

    # Brüt çekim: nakit * (1 + surcharge%)
    gross_charge = round(cash_given * (1 + surcharge_pct/100.0), 2)
    row = banks[banks["name"]==adv_bank].iloc[0]
    fee_pct = float(row["fee_cash_adv_pct"])
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

    note_adv = st.text_input("Not", key="adv_note")
    if st.button("Kaydet (Kart→Nakit)", key="btn_save_adv"):
        # 1) POS brüt tahsilat (INFLOW), bankaya yarın net yatacak
        add_payment("CASH_ADV", adv_bank, gross_charge, fee_pct, "INFLOW", f"CashAdv: {note_adv}", sale_id=None, settle_days=settle_days)
        # 2) Nakit çıkışı (kasadan müşteriye)
        add_payment("CASH", None, cash_given, 0.0, "OUTFLOW", f"CashAdv payout: {note_adv}", sale_id=None)
        st.success("Kart→Nakit işlemi kaydedildi.")

# ----- 4) Transfers -----
with tabs[3]:
    st.subheader("Kasa ⇄ Banka Transferleri")
    ttype = st.selectbox("Tür", ["KASA → BANKA", "BANKA → KASA"], key="trf_type")
    bname = st.selectbox("Banka", list(banks_df()["name"]), key="trf_bank")
    amt = st.number_input("Tutar (₺)", min_value=0.0, step=50.0, key="trf_amt")
    note = st.text_input("Not", key="trf_note")
    if st.button("Transferi Kaydet", key="btn_trf"):
        if ttype.startswith("KASA"):
            add_transfer("CASH_TO_BANK", bname, amt, note)
        else:
            add_transfer("BANK_TO_CASH", bname, amt, note)
        st.success("Transfer kaydedildi.")

    st.markdown("#### Son Transferler")
    st.dataframe(qdf("SELECT tdate, ttype, bank, amount, note FROM transfers ORDER BY id DESC LIMIT 30"),
                 use_container_width=True)

# ----- 5) Reports -----
with tabs[4]:
    st.subheader("Bakiyeler ve POS Ekstre")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Kasa (₺)**")
        st.metric("Kasa Bakiye", f"{cash_balance():,.2f} ₺")
    with c2:
        st.markdown("**Banka Bakiyeleri (Yatanlar Dahil)**")
        bs = bank_balances(include_pending=False)
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
        start = st.date_input("Başlangıç", value=date.today().replace(day=1), key="r_start")
    with d2:
        end = st.date_input("Bitiş", value=date.today(), key="r_end")
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
        # Kâr = brüt - fee - verilen nakit; verilen nakit ayrı CASH OUTFLOW kaydında.
        # Aynı gün içinde “CashAdv payout” outflow toplamını yaklaştırma olarak düşelim.
        # Daha sağlamı: future improvement = link id'si ile eşleştirmek.
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