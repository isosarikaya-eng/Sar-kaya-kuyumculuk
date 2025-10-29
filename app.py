# -*- coding: utf-8 -*-
import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Sarıkaya Kuyumculuk — Satış/POS/Özbağ/Envanter", layout="wide")
DB = "sarikaya_kuyum.db"

def NOW():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- DB helpers ----------------
def conn():
    return sqlite3.connect(DB, check_same_thread=False)

def run(sql: str, params: tuple = ()):
    with conn() as c:
        c.execute(sql, params); c.commit()

def qdf(sql: str, params: tuple = ()) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(sql, c, params=params)

# ---------------- Schema ----------------
def ensure_schema():
    # Banks (POS commissions)
    run("""CREATE TABLE IF NOT EXISTS banks(
        name TEXT PRIMARY KEY,
        pos_sale_pct REAL NOT NULL,
        cash_adv_pct REAL NOT NULL,
        settle_days INTEGER NOT NULL
    )""")

    # Openings
    run("""CREATE TABLE IF NOT EXISTS openings(
        id INTEGER PRIMARY KEY CHECK(id=1),
        cash_tl REAL NOT NULL DEFAULT 0
    )""")
    if qdf("SELECT COUNT(*) n FROM openings").iloc[0,0] == 0:
        run("INSERT INTO openings(id,cash_tl) VALUES(1,0)")

    run("""CREATE TABLE IF NOT EXISTS bank_openings(
        bank TEXT PRIMARY KEY,
        balance_tl REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(bank) REFERENCES banks(name)
    )""")

    # Products (unit: 'adet' or 'gr')
    run("""CREATE TABLE IF NOT EXISTS products(
        name TEXT PRIMARY KEY,
        unit TEXT NOT NULL
    )""")

    # Stock moves (+/-)
    run("""CREATE TABLE IF NOT EXISTS stock_moves(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        mdate TEXT NOT NULL,
        product TEXT NOT NULL,
        qty REAL NOT NULL,
        unit TEXT NOT NULL,
        note TEXT
    )""")

    # Sales + items + payments
    run("""CREATE TABLE IF NOT EXISTS sales(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        sdate TEXT NOT NULL,
        customer TEXT,
        note TEXT
    )""")
    run("""CREATE TABLE IF NOT EXISTS sale_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL,
        product TEXT NOT NULL,
        qty REAL NOT NULL,
        unit TEXT NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        FOREIGN KEY(sale_id) REFERENCES sales(id)
    )""")
    # method: CASH / TRANSFER / CARD / CASH_ADV
    # direction: INFLOW / OUTFLOW
    run("""CREATE TABLE IF NOT EXISTS payments(
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
        direction TEXT NOT NULL,
        note TEXT
    )""")

    # Transfers
    run("""CREATE TABLE IF NOT EXISTS transfers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        tdate TEXT NOT NULL,
        ttype TEXT NOT NULL,   -- CASH_TO_BANK / BANK_TO_CASH
        bank TEXT,
        amount REAL NOT NULL,
        note TEXT
    )""")

    # Özbağ entries (all products, mil-based)
    run("""CREATE TABLE IF NOT EXISTS ozbag_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        bdate TEXT NOT NULL,
        product TEXT NOT NULL,
        qty REAL NOT NULL,
        qty_unit TEXT NOT NULL,     -- 'adet' or 'gr' (as entered)
        per_item_gram REAL,         -- if qty_unit='adet', needed
        mil REAL NOT NULL,
        gram_total REAL NOT NULL,   -- computed: qty*per_item_gram or qty
        has_rate REAL NOT NULL,     -- ₺/HAS (manual)
        workmanship_tl REAL NOT NULL,
        has_equiv REAL NOT NULL,    -- gram_total * mil/1000
        total_tl REAL NOT NULL,
        note TEXT
    )""")

    # Özbağ HAS net (vendor balance): + Özbağ size borçlu, - siz Özbağ'a
    run("""CREATE TABLE IF NOT EXISTS ozbag_balance(
        id INTEGER PRIMARY KEY CHECK(id=1),
        has_net REAL NOT NULL
    )""")
    if qdf("SELECT COUNT(*) n FROM ozbag_balance").iloc[0,0] == 0:
        run("INSERT INTO ozbag_balance(id,has_net) VALUES(1,0.0)")

    # HAS wallet (profit accumulation)
    run("""CREATE TABLE IF NOT EXISTS has_wallet(
        id INTEGER PRIMARY KEY CHECK(id=1),
        has_balance REAL NOT NULL
    )""")
    if qdf("SELECT COUNT(*) n FROM has_wallet").iloc[0,0] == 0:
        run("INSERT INTO has_wallet(id,has_balance) VALUES(1,0.0)")

    # Defaults
    defaults = [
        ("Vakıfbank",      0.0, 2.8, 1),
        ("İş Bankası",     0.0, 3.6, 1),
        ("Ziraat Bankası", 0.0, 3.6, 1),
        ("QNB Finansbank", 0.0, 3.6, 1),
    ]
    for n, sp, ap, d in defaults:
        if qdf("SELECT COUNT(*) n FROM banks WHERE name=?", (n,)).iloc[0,0] == 0:
            run("INSERT INTO banks(name,pos_sale_pct,cash_adv_pct,settle_days) VALUES(?,?,?,?)",
                (n, sp, ap, d))
        if qdf("SELECT COUNT(*) n FROM bank_openings WHERE bank=?", (n,)).iloc[0,0] == 0:
            run("INSERT INTO bank_openings(bank,balance_tl) VALUES(?,?)", (n, 0.0))

    base_products = [
        ("Çeyrek Altın","adet"),
        ("Yarım Altın","adet"),
        ("Tam Altın","adet"),
        ("Ata Lira","adet"),
        ("24 Ayar Gram","gr"),
        ("22 Ayar Gram","gr"),
        ("22 Ayar 0,5 gr","adet"),
        ("22 Ayar 0,25 gr","adet"),
        ("Bilezik 22K","gr"),
        ("Hurda 22 Ayar","gr")
    ]
    for n,u in base_products:
        if qdf("SELECT COUNT(*) n FROM products WHERE name=?", (n,)).iloc[0,0] == 0:
            run("INSERT INTO products(name,unit) VALUES(?,?)", (n,u))

ensure_schema()

# --------------- helpers ---------------
def banks_df(): return qdf("SELECT * FROM banks ORDER BY name")
def bank_openings_df(): return qdf("SELECT * FROM bank_openings ORDER BY bank")
def products_df(): return qdf("SELECT name,unit FROM products ORDER BY name")
def cash_opening(): return float(qdf("SELECT cash_tl FROM openings WHERE id=1").iloc[0,0])
def set_cash_open(v:float): run("UPDATE openings SET cash_tl=? WHERE id=1",(float(v),))
def set_bank_open(b:str,v:float): run("UPDATE bank_openings SET balance_tl=? WHERE bank=?", (float(v),b))
def update_bank(n:str,sp:float,ap:float,days:int): run(
    "UPDATE banks SET pos_sale_pct=?, cash_adv_pct=?, settle_days=? WHERE name=?",(sp,ap,days,n)
)
def add_stock(product:str, qty:float, unit:str, note:str):
    run("""INSERT INTO stock_moves(ts,mdate,product,qty,unit,note)
           VALUES(?,?,?,?,?,?)""",(NOW(), date.today().isoformat(), product, qty, unit, note))
def stock_summary():
    return qdf("""SELECT product, unit, SUM(qty) qty FROM stock_moves GROUP BY product,unit ORDER BY product""")

def add_sale_header(customer:str, note:str)->int:
    run("INSERT INTO sales(ts,sdate,customer,note) VALUES(?,?,?,?)",(NOW(), date.today().isoformat(), customer, note))
    return int(qdf("SELECT last_insert_rowid() id").iloc[0,0])

def add_sale_item(sale_id:int, product:str, qty:float, unit:str, unit_price:float):
    lt = round(qty*unit_price,2)
    run("""INSERT INTO sale_items(sale_id,product,qty,unit,unit_price,line_total)
           VALUES(?,?,?,?,?,?)""",(sale_id,product,qty,unit,unit_price,lt))

def add_payment(method:str, direction:str, gross:float,
                bank:Optional[str]=None, fee_pct:float=0.0, settle_days:int=0,
                note:str="", sale_id:Optional[int]=None):
    fee_amount = round(gross*fee_pct/100.0,2)
    net_settle = round(gross - fee_amount, 2)
    settle = (date.today()+timedelta(days=settle_days)).isoformat() if bank else None
    run("""INSERT INTO payments(ts,pdate,sale_id,method,bank,gross_amount,fee_pct,fee_amount,net_settlement,settle_date,direction,note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (NOW(), date.today().isoformat(), sale_id, method, bank, gross, fee_pct, fee_amount, net_settle, settle, direction, note))

def add_transfer(ttype:str, bank:str, amount:float, note:str):
    run("INSERT INTO transfers(ts,tdate,ttype,bank,amount,note) VALUES(?,?,?,?,?,?)",
        (NOW(), date.today().isoformat(), ttype, bank, amount, note))

def ozbag_net()->float: return float(qdf("SELECT has_net FROM ozbag_balance WHERE id=1").iloc[0,0])
def set_ozbag_net(v:float): run("UPDATE ozbag_balance SET has_net=? WHERE id=1",(float(v),))
def has_wallet()->float: return float(qdf("SELECT has_balance FROM has_wallet WHERE id=1").iloc[0,0])
def set_has_wallet(v:float): run("UPDATE has_wallet SET has_balance=? WHERE id=1",(float(v),))

def add_ozbag_entry(product:str, qty:float, qty_unit:str, per_item_gram:Optional[float],
                    mil:float, has_rate:float, workmanship_tl:float, note:str, add_to_cari:bool):
    gram_total = qty*per_item_gram if qty_unit=="adet" else qty
    has_equiv = round(gram_total * (mil/1000.0), 3)
    total_tl = round(has_equiv*has_rate + workmanship_tl, 2)
    run("""INSERT INTO ozbag_entries(ts,bdate,product,qty,qty_unit,per_item_gram,mil,gram_total,has_rate,workmanship_tl,has_equiv,total_tl,note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (NOW(), date.today().isoformat(), product, qty, qty_unit, per_item_gram, mil, gram_total, has_rate, workmanship_tl, has_equiv, total_tl, note))
    # stok artır
    real_qty = gram_total if products_df().set_index("name").loc[product,"unit"]=="gr" else qty
    real_unit = products_df().set_index("name").loc[product,"unit"]
    add_stock(product, real_qty, real_unit, f"Özbağ girişi {mil}‰")
    # ozbag cari (HAS borç)
    if add_to_cari:
        set_ozbag_net(ozbag_net() - has_equiv)
    return has_equiv, total_tl, gram_total

# --------------- Balance engines ---------------
def cash_balance()->float:
    bal = cash_opening()
    df = qdf("SELECT direction,gross_amount FROM payments WHERE method='CASH'")
    for _,r in df.iterrows():
        a=float(r["gross_amount"]); bal += a if r["direction"]=="INFLOW" else -a
    tr=qdf("SELECT ttype,amount FROM transfers")
    for _,r in tr.iterrows():
        a=float(r["amount"])
        bal += a if r["ttype"]=="BANK_TO_CASH" else -a
    return round(bal,2)

def bank_balances(include_pending:bool=False)->Dict[str,float]:
    base=bank_openings_df().set_index("bank")["balance_tl"].astype(float).to_dict()
    if include_pending:
        df=qdf("""SELECT bank,net_settlement FROM payments
                 WHERE bank IS NOT NULL AND method IN ('CARD','CASH_ADV')""")
    else:
        today=date.today().isoformat()
        df=qdf("""SELECT bank,net_settlement FROM payments
                 WHERE bank IS NOT NULL AND method IN ('CARD','CASH_ADV')
                 AND settle_date<=?""",(today,))
    for _,r in df.iterrows():
        b=r["bank"]; base[b]=base.get(b,0.0)+float(r["net_settlement"])
    tr=qdf("SELECT bank,ttype,amount FROM transfers")
    for _,r in tr.iterrows():
        a=float(r["amount"]); b=r["bank"]
        base[b]=base.get(b,0.0)+(a if r["ttype"]=="CASH_TO_BANK" else -a)
    return {k:round(v,2) for k,v in base.items()}

def today_settlements()->pd.DataFrame:
    t=date.today().isoformat()
    return qdf("""SELECT pdate,bank,method,gross_amount,fee_pct,fee_amount,net_settlement,settle_date,note
                  FROM payments WHERE bank IS NOT NULL AND settle_date=?
                  ORDER BY bank,pdate""",(t,))

def pending_settlements()->pd.DataFrame:
    t=date.today().isoformat()
    return qdf("""SELECT pdate,bank,method,gross_amount,fee_pct,fee_amount,net_settlement,settle_date,note
                  FROM payments WHERE bank IS NOT NULL AND settle_date>?
                  ORDER BY settle_date,bank""",(t,))

# ---------------- UI ----------------
st.title("💎 Sarıkaya Kuyumculuk — Satış / POS / Özbağ / Envanter")

tabs = st.tabs([
    "⚙️ Ayarlar",
    "🛒 Normal Satış (tek ekranda ödeme)",
    "🧾 Parçalı Satış (sepet + ödeme)",
    "💳 POS → Nakit (tek geçiş, kâr HAS’a)",
    "🏛️ Özbağ Girişi (tüm ürünlerde milyem)",
    "📦 Envanter (HAS/₺ değeri)",
    "🔁 Kasa ⇄ Banka",
    "📊 Raporlar"
])

# ----- Ayarlar -----
with tabs[0]:
    st.subheader("Açılış Bakiyeleri")
    c1,c2=st.columns([1,2])
    with c1:
        op = st.number_input("Kasa Açılış (₺)", min_value=0.0, step=100.0, value=float(cash_opening()))
        if st.button("Kasa Açılış Kaydet"):
            set_cash_open(op); st.success("Güncellendi.")
    with c2:
        st.markdown("**Banka Açılışları (₺)**")
        bdf=bank_openings_df()
        for _,row in bdf.iterrows():
            nm=row["bank"]
            val=st.number_input(nm, min_value=0.0, value=float(row["balance_tl"]), step=100.0, key=f"bo_{nm}")
            if st.button(f"{nm} güncelle", key=f"bo_btn_{nm}"):
                set_bank_open(nm,val); st.success(f"{nm} güncellendi.")

    st.divider()
    st.subheader("POS Ayarları")
    b=banks_df()
    for _,r in b.iterrows():
        n=r["name"]; cols=st.columns([2,1,1,1])
        cols[0].markdown(f"**{n}**")
        sp=cols[1].number_input("Satış POS %", min_value=0.0, value=float(r["pos_sale_pct"]), step=0.1, key=f"sp_{n}")
        ap=cols[2].number_input("Kart→Nakit %", min_value=0.0, value=float(r["cash_adv_pct"]), step=0.1, key=f"ap_{n}")
        sd=cols[3].number_input("Yatış (gün)", min_value=0, value=int(r["settle_days"]), step=1, key=f"sd_{n}")
        if st.button(f"{n} kaydet", key=f"bank_save_{n}"):
            update_bank(n,sp,ap,sd); st.success("Kaydedildi.")

# ----- Normal Satış (tek ürün + aynı ekranda ödeme) -----
with tabs[1]:
    st.subheader("🛒 Normal Satış — Ürün + Ödeme (aynı ekran)")
    prods=products_df()
    colA,colB,colC=st.columns(3)
    with colA:
        prod=st.selectbox("Ürün", list(prods["name"]), key="ns_prod")
        unit = prods[prods["name"]==prod].iloc[0]["unit"]
    with colB:
        qty = st.number_input(f"Miktar ({unit})", min_value=0.0, step=1.0, key="ns_qty")
    with colC:
        uprice = st.number_input("Birim Fiyat (₺)", min_value=0.0, step=10.0, key="ns_price")
    st.caption("Not: Bir adetlik hızlı satış ekranı. Çok kalem için 'Parçalı Satış' sekmesini kullanın.")

    # ödeme bacakları
    if "ns_legs" not in st.session_state: st.session_state["ns_legs"]=[]
    st.markdown("#### Ödeme Bacakları")
    lc=st.columns([2,2,2,2,2])
    leg_m = lc[0].selectbox("Yöntem", ["NAKIT","HAVALE","KART"], key="ns_leg_m")
    leg_a = lc[1].number_input("Tutar (₺)", min_value=0.0, step=10.0, key="ns_leg_a")
    leg_b=None; fee_pct=0.0; settle_days=0
    if leg_m=="KART":
        bdf=banks_df()
        leg_b=lc[2].selectbox("Banka", list(bdf["name"]), key="ns_leg_bank")
        fee_pct=float(bdf[bdf["name"]==leg_b].iloc[0]["pos_sale_pct"])
        settle_days=int(bdf[bdf["name"]==leg_b].iloc[0]["settle_days"])
        lc[3].number_input("Komisyon %", min_value=0.0, value=fee_pct, step=0.1, key="ns_leg_fee_ro", disabled=True)
        lc[4].number_input("Yatış (gün)", min_value=0, value=settle_days, step=1, key="ns_leg_delay_ro", disabled=True)
    else:
        lc[2].text_input("Banka","-", disabled=True, key="ns_leg_bank_dummy")
        lc[3].number_input("Komisyon %",0.0,0.0, step=0.1, key="ns_leg_fee_dummy", disabled=True)
        lc[4].number_input("Yatış (gün)",0,0, step=1, key="ns_leg_delay_dummy", disabled=True)

    if st.button("Bacak Ekle", key="ns_add_leg"):
        st.session_state["ns_legs"].append({"method":leg_m,"amount":leg_a,"bank":leg_b})

    if st.session_state["ns_legs"]:
        st.dataframe(pd.DataFrame(st.session_state["ns_legs"]), use_container_width=True)

    customer=st.text_input("Müşteri (ops.)", key="ns_cust")
    note=st.text_input("Not", key="ns_note")
    total = round(qty*uprice,2)
    st.metric("Satış Toplam", f"{total:,.2f} ₺")

    if st.button("Satışı Kaydet", key="ns_save"):
        if total<=0:
            st.error("Satış toplamı > 0 olmalı.")
        else:
            # satış başlık + tek kalem
            sid=add_sale_header(customer, note)
            add_sale_item(sid, prod, float(qty), unit, float(uprice))
            # stok düş
            add_stock(prod, -float(qty), unit, f"Satış #{sid}")
            # ödemeler
            legs_total=sum(x["amount"] for x in st.session_state["ns_legs"])
            if abs(legs_total-total)>0.01:
                st.warning(f"Ödeme bacak toplamı ({legs_total:.2f}) satış toplamına ({total:.2f}) eşit değil.")
            bdf=banks_df()
            for leg in st.session_state["ns_legs"]:
                m=leg["method"]; amt=float(leg["amount"])
                if m=="NAKIT":
                    add_payment("CASH","INFLOW",amt,note=f"Sale #{sid}",sale_id=sid)
                elif m=="HAVALE":
                    add_payment("TRANSFER","INFLOW",amt,note=f"Sale #{sid} (havale)",sale_id=sid)
                else:
                    bname=leg["bank"]; row=bdf[bdf["name"]==bname].iloc[0]
                    add_payment("CARD","INFLOW",amt,bank=bname,fee_pct=float(row["pos_sale_pct"]),
                                settle_days=int(row["settle_days"]),note=f"Sale #{sid} (kart)",sale_id=sid)
            st.success(f"Satış kaydedildi (#{sid}).")
            st.session_state["ns_legs"]=[]

# ----- Parçalı Satış (sepet + ödeme) -----
with tabs[2]:
    st.subheader("🧾 Parçalı Satış — Kalem Ekle + Ödeme")
    if "cart" not in st.session_state: st.session_state["cart"]=[]
    prods=products_df()
    c=st.columns([3,1,1,1,1])
    p_sel=c[0].selectbox("Ürün", list(prods["name"]), key="ps_prod")
    p_unit=prods[prods["name"]==p_sel].iloc[0]["unit"]
    qty=c[1].number_input(f"Miktar ({p_unit})", min_value=0.0, step=1.0, key="ps_qty")
    up=c[2].number_input("Birim Fiyat (₺)", min_value=0.0, step=10.0, key="ps_price")
    if st.button("Kalem Ekle", key="ps_add"):
        st.session_state["cart"].append({"product":p_sel,"qty":qty,"unit":p_unit,"unit_price":up})
    if st.session_state["cart"]:
        df=pd.DataFrame(st.session_state["cart"]); df["line_total"]=(df["qty"]*df["unit_price"]).round(2)
        st.dataframe(df, use_container_width=True)
        total=float(df["line_total"].sum()); st.metric("Sepet Toplam", f"{total:,.2f} ₺")
    else:
        st.info("Sepette kalem yok.")

    # ödeme bacakları
    if "ps_legs" not in st.session_state: st.session_state["ps_legs"]=[]
    lc=st.columns([2,2,2,2,2])
    leg_m=lc[0].selectbox("Yöntem",["NAKIT","HAVALE","KART"], key="ps_leg_m")
    leg_a=lc[1].number_input("Tutar (₺)",min_value=0.0, step=10.0, key="ps_leg_a")
    leg_b=None; fee_pct=0.0; settle_days=0
    if leg_m=="KART":
        bdf=banks_df()
        leg_b=lc[2].selectbox("Banka", list(bdf["name"]), key="ps_leg_bank")
        fee_pct=float(bdf[bdf["name"]==leg_b].iloc[0]["pos_sale_pct"])
        settle_days=int(bdf[bdf["name"]==leg_b].iloc[0]["settle_days"])
        lc[3].number_input("Komisyon %", min_value=0.0, value=fee_pct, step=0.1, key="ps_leg_fee_ro", disabled=True)
        lc[4].number_input("Yatış (gün)", min_value=0, value=settle_days, step=1, key="ps_leg_delay_ro", disabled=True)
    else:
        lc[2].text_input("Banka","-",disabled=True, key="ps_leg_bank_dummy")
        lc[3].number_input("Komisyon %",0.0,0.0, step=0.1, key="ps_leg_fee_dummy", disabled=True)
        lc[4].number_input("Yatış (gün)",0,0, step=1, key="ps_leg_delay_dummy", disabled=True)
    if st.button("Ödeme Bacağı Ekle", key="ps_add_leg"):
        st.session_state["ps_legs"].append({"method":leg_m,"amount":leg_a,"bank":leg_b})
    if st.session_state["ps_legs"]:
        st.dataframe(pd.DataFrame(st.session_state["ps_legs"]), use_container_width=True)

    cust=st.text_input("Müşteri (ops.)", key="ps_cust")
    note=st.text_input("Not", key="ps_note")

    if st.button("Parçalı Satışı Kaydet", key="ps_save"):
        if not st.session_state["cart"]:
            st.error("Sepet boş.")
        else:
            sid=add_sale_header(cust,note)
            for r in st.session_state["cart"]:
                add_sale_item(sid,r["product"],float(r["qty"]),r["unit"],float(r["unit_price"]))
                add_stock(r["product"], -float(r["qty"]), r["unit"], f"Satış #{sid}")
            total=float(sum(x["qty"]*x["unit_price"] for x in st.session_state["cart"]))
            legs_total=sum(x["amount"] for x in st.session_state["ps_legs"])
            if abs(legs_total-total)>0.01:
                st.warning(f"Ödeme bacak toplamı ({legs_total:.2f}) satış toplamına ({total:.2f}) eşit değil.")
            bdf=banks_df()
            for leg in st.session_state["ps_legs"]:
                m=leg["method"]; amt=float(leg["amount"])
                if m=="NAKIT": add_payment("CASH","INFLOW",amt,note=f"Sale #{sid}", sale_id=sid)
                elif m=="HAVALE": add_payment("TRANSFER","INFLOW",amt,note=f"Sale #{sid} (havale)", sale_id=sid)
                else:
                    bname=leg["bank"]; row=bdf[bdf["name"]==bname].iloc[0]
                    add_payment("CARD","INFLOW",amt,bank=bname,fee_pct=float(row["pos_sale_pct"]),
                                settle_days=int(row["settle_days"]),note=f"Sale #{sid} (kart)",sale_id=sid)
            st.success(f"Parçalı satış kaydedildi (#{sid}).")
            st.session_state["cart"]=[]; st.session_state["ps_legs"]=[]

# ----- POS → Nakit (tek geçiş, kâr HAS'a) -----
with tabs[3]:
    st.subheader("💳 Karttan Çekip Nakit Verme — POS → Nakit")
    bdf=banks_df()
    col1,col2,col3 = st.columns(3)
    bank = col1.selectbox("Banka", list(bdf["name"]), key="adv_bank")
    charged = col2.number_input("POS'tan Geçilen Tutar (₺)", min_value=0.0, step=50.0, key="adv_charged")
    cash_given = col3.number_input("Müşteriye Verilen Nakit (₺)", min_value=0.0, step=50.0, key="adv_cash")

    row = bdf[bdf["name"]==bank].iloc[0]
    fee_pct = float(row["cash_adv_pct"])
    settle_days=int(row["settle_days"])
    fee_amount = round(charged*fee_pct/100.0,2)
    net_settle = round(charged - fee_amount, 2)
    profit_tl = round(charged - fee_amount - cash_given, 2)

    st.markdown("#### Hesaplama")
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Kesinti (%)", f"{fee_pct:.1f}%")
    m2.metric("Banka Kesintisi", f"{fee_amount:,.2f} ₺")
    m3.metric("Ertesi Gün Yatan (Net)", f"{net_settle:,.2f} ₺")
    m4.metric("Kâr (TL)", f"{profit_tl:,.2f} ₺")

    has_rate = st.number_input("HAS Kuru (₺/HAS)", min_value=0.0, step=1.0, key="adv_has_rate")
    profit_has = round((profit_tl/has_rate), 3) if has_rate>0 else 0.0
    st.metric("Kâr (HAS)", f"{profit_has:.3f} HAS")

    note = st.text_input("Not", key="adv_note")

    if st.button("Kaydet (POS→Nakit)", key="adv_save"):
        # 1) POS brüt tahsilat (INFLOW) -> bankaya yarın net yatacak
        add_payment("CASH_ADV","INFLOW",charged, bank=bank, fee_pct=fee_pct, settle_days=settle_days, note=f"CashAdv: {note}")
        # 2) Nakit çıkışı (kasadan)
        add_payment("CASH","OUTFLOW",cash_given, note=f"CashAdv payout: {note}")
        # 3) Kârı HAS cüzdanına ekle
        set_has_wallet(has_wallet() + profit_has)
        st.success(f"Kaydedildi. Kâr: {profit_tl:,.2f} ₺ ≈ {profit_has:.3f} HAS")

# ----- Özbağ (genel, milyem bazlı) -----
with tabs[4]:
    st.subheader("🏛️ Özbağ — Tüm Ürünlerde Milyem Bazında Giriş")
    prods=products_df()
    cc1,cc2,cc3=st.columns(3)
    with cc1:
        p=st.selectbox("Ürün", list(prods["name"]), key="oz_p")
        base_unit = prods[prods["name"]==p].iloc[0]["unit"]
        qty_unit = st.selectbox("Miktar Birimi", ["adet","gr"], index=0 if base_unit=="adet" else 1, key="oz_qty_unit")
    with cc2:
        qty=st.number_input("Miktar", min_value=0.0, step=1.0, key="oz_qty")
        per_item_gram=None
        if qty_unit=="adet":
            per_item_gram=st.number_input("Birim Gram (adet ürünler için)", min_value=0.0, step=0.01, key="oz_pig")
        mil=st.number_input("Milyem (‰)", min_value=800.0, value=916.0, step=0.5, key="oz_mil")
    with cc3:
        has_rate=st.number_input("HAS Kuru (₺/HAS)", min_value=0.0, step=1.0, key="oz_has")
        work=st.number_input("İşçilik (₺)", min_value=0.0, step=10.0, key="oz_work")
        add_cari=st.checkbox("Özbağ cariye borç yaz (HAS)", value=True, key="oz_cari")

    note=st.text_input("Not", key="oz_note")
    if st.button("Girişi Kaydet", key="oz_save"):
        he, tt, gtot = add_ozbag_entry(p,float(qty),qty_unit, per_item_gram if qty_unit=="adet" else None,
                                       float(mil), float(has_rate), float(work), note, bool(add_cari))
        st.success(f"Girdi: {p} → {gtot:.2f} gr, {mil:.1f}‰ → {he:.3f} HAS • Toplam {tt:,.2f} ₺")

    st.markdown("#### Son 20 Özbağ Girişi")
    st.dataframe(qdf("""SELECT bdate, product, qty, qty_unit, per_item_gram, mil, gram_total, has_rate, workmanship_tl, has_equiv, total_tl, note
                        FROM ozbag_entries ORDER BY id DESC LIMIT 20"""), use_container_width=True)

# ----- Envanter -----
with tabs[5]:
    st.subheader("📦 Envanter Değerleme (HAS / ₺)")
    st.caption("Aşağıda ürün bazında **milyem** ve (adet ürünler için) **birim gram** gir; HAS kuru (₺/HAS) ile toplam değer hesaplanır.")
    HAS = st.number_input("HAS Kuru (₺/HAS)", min_value=0.0, step=1.0, key="inv_has")
    stock = stock_summary()
    if stock.empty:
        st.info("Stok hareketi yok.")
    else:
        prods=products_df().set_index("name")
        rows=[]
        for _,r in stock.iterrows():
            name=r["product"]; unit=r["unit"]; qty=float(r["qty"])
            c1,c2,c3,c4 = st.columns([3,1.2,1.2,1.2])
            c1.markdown(f"**{name}** — {qty:.3f} {unit}")
            mil = c2.number_input(f"{name} milyem", min_value=800.0, value=916.0 if "22" in name else 995.0, step=0.5, key=f"inv_mil_{name}")
            per_item_gram= None
            if unit=="adet":
                per_item_gram = c3.number_input(f"{name} birim gr", min_value=0.0, step=0.01, key=f"inv_pig_{name}")
            # compute HAS equiv
            gram_total = qty if unit=="gr" else qty*(per_item_gram or 0.0)
            has_eq = gram_total * (mil/1000.0)
            tl_val = has_eq * HAS
            c4.metric("₺ Değer", f"{tl_val:,.2f} ₺")
            rows.append([name, unit, qty, mil, per_item_gram if unit=="adet" else None, gram_total, has_eq, tl_val])

        df=pd.DataFrame(rows, columns=["Ürün","Birim","Miktar","Milyem","BirimGr","ToplamGr","HAS","₺Değer"])
        st.dataframe(df, use_container_width=True)
        st.metric("Toplam Envanter (₺)", f"{df['₺Değer'].sum():,.2f} ₺")

# ----- Kasa ⇄ Banka -----
with tabs[6]:
    st.subheader("🔁 Kasa ⇄ Banka Transferleri")
    ttype=st.selectbox("Tür",["KASA → BANKA","BANKA → KASA"], key="trf_type")
    bname=st.selectbox("Banka", list(banks_df()["name"]), key="trf_bank")
    amt=st.number_input("Tutar (₺)", min_value=0.0, step=50.0, key="trf_amt")
    note=st.text_input("Not", key="trf_note")
    if st.button("Transferi Kaydet", key="trf_save"):
        add_transfer("CASH_TO_BANK" if ttype.startswith("KASA") else "BANK_TO_CASH", bname, float(amt), note)
        st.success("Transfer kaydedildi.")
    st.markdown("#### Son Transferler")
    st.dataframe(qdf("SELECT tdate, ttype, bank, amount, note FROM transfers ORDER BY id DESC LIMIT 30"), use_container_width=True)

# ----- Raporlar -----
with tabs[7]:
    st.subheader("📊 Bakiyeler")
    c1,c2,c3 = st.columns(3)
    c1.metric("Kasa (₺)", f"{cash_balance():,.2f} ₺")
    banks_bal=bank_balances(include_pending=False)
    c2.markdown("**Bankalar (yatanlar dahil)**")
    if banks_bal:
        for k,v in banks_bal.items(): c2.metric(k, f"{v:,.2f} ₺")
    else:
        c2.info("Banka yok.")
    c3.metric("HAS Cüzdanı (kâr birikimi)", f"{has_wallet():,.3f} HAS")

    st.divider()
    st.markdown("### Bugün Yatacak POS (Net)")
    td=today_settlements()
    if td.empty: st.info("Bugün yok.")
    else:
        st.dataframe(td, use_container_width=True)
        st.metric("Toplam Net", f"{td['net_settlement'].sum():,.2f} ₺")

    st.markdown("### Bekleyen POS")
    pend=pending_settlements()
    if pend.empty: st.info("Bekleyen yok.")
    else: st.dataframe(pend, use_container_width=True)

    st.divider()
    st.markdown("### POS Komisyon Giderleri (Tarih Aralığı)")
    d1,d2=st.columns(2)
    start=d1.date_input("Başlangıç", value=date.today().replace(day=1))
    end=d2.date_input("Bitiş", value=date.today())
    rep=qdf("""SELECT pdate,bank,method,gross_amount,fee_pct,fee_amount
               FROM payments WHERE bank IS NOT NULL AND pdate BETWEEN ? AND ?""",(start.isoformat(), end.isoformat()))
    if rep.empty: st.info("Kayıt yok.")
    else:
        st.dataframe(rep, use_container_width=True)
        st.metric("Toplam Komisyon", f"{rep['fee_amount'].sum():,.2f} ₺")