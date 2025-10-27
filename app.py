import sqlite3
from contextlib import closing
from datetime import datetime, date
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

# =============== GENEL ===============
st.set_page_config(page_title="Sarıkaya Kuyumculuk — Kâr/Zarar • Envanter • Borç/Alacak", layout="wide")
DB_PATH = "data.db"

PRODUCTS = [
    ("CEYREK", "Çeyrek Altın", "adet"),
    ("YARIM",  "Yarım Altın",   "adet"),
    ("TAM",    "Tam Altın",     "adet"),
    ("ATA",    "Ata Lira",      "adet"),
    ("G24",    "24 Ayar Gram",  "gr"),
    ("G22",    "22 Ayar Gram",  "gr"),
    ("G22_05", "22 Ayar 0,5 gr","gr"),
    ("G22_025","22 Ayar 0,25 gr","gr"),
]

# =============== DB ===============
def conn():
    return sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)

def ensure_db():
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        # ürünler
        cur.execute("""
        CREATE TABLE IF NOT EXISTS products(
          code TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          unit TEXT NOT NULL
        )""")
        # açılış stokları
        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening(
          product_code TEXT PRIMARY KEY,
          qty REAL NOT NULL DEFAULT 0,
          unit_cost REAL NOT NULL DEFAULT 0,
          FOREIGN KEY(product_code) REFERENCES products(code)
        )""")
        # açılış TL (tek satır)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening_cash(
          id INTEGER PRIMARY KEY CHECK(id=1),
          amount REAL NOT NULL DEFAULT 0
        )""")
        cur.execute("INSERT OR IGNORE INTO opening_cash(id, amount) VALUES(1, 0)")

        # alış/satış işlemleri
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          tdate TEXT NOT NULL,
          ttype TEXT NOT NULL,        -- BUY / SELL
          product_code TEXT NOT NULL,
          qty REAL NOT NULL,
          unit_price REAL NOT NULL,
          note TEXT,
          FOREIGN KEY(product_code) REFERENCES products(code)
        )""")

        # NAKİT: tahsilat/ödeme
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cash_moves(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          tdate TEXT NOT NULL,
          mtype TEXT NOT NULL,        -- TAHSILAT / ODEME
          person TEXT,
          amount REAL NOT NULL,
          note TEXT
        )""")

        # GRAM BAZLI: borç/alacak (kişi-ürün-gram)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS liabilities(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          tdate TEXT NOT NULL,
          side TEXT NOT NULL,         -- ALACAK (bize borçlu) / BORC (biz borçluyuz)
          person TEXT NOT NULL,
          product_code TEXT NOT NULL,
          grams REAL NOT NULL,
          note TEXT,
          FOREIGN KEY(product_code) REFERENCES products(code)
        )""")

        # seed ürünler & opening
        cur.execute("SELECT COUNT(1) FROM products")
        if cur.fetchone()[0] == 0:
            cur.executemany("INSERT INTO products(code,name,unit) VALUES(?,?,?)", PRODUCTS)

        cur.execute("SELECT product_code FROM opening")
        existing = {r[0] for r in cur.fetchall()}
        missing = [(p[0], 0.0, 0.0) for p in PRODUCTS if p[0] not in existing]
        if missing:
            cur.executemany("INSERT INTO opening(product_code,qty,unit_cost) VALUES(?,?,?)", missing)
        c.commit()

def df_products() -> pd.DataFrame:
    with closing(conn()) as c:
        return pd.read_sql_query("SELECT code,name,unit FROM products ORDER BY rowid", c)

def get_opening() -> pd.DataFrame:
    with closing(conn()) as c:
        q = """
        SELECT p.code, p.name, p.unit, o.qty, o.unit_cost
        FROM opening o JOIN products p ON p.code=o.product_code
        ORDER BY p.rowid"""
        return pd.read_sql_query(q, c)

def save_opening(df: pd.DataFrame):
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        for _, r in df.iterrows():
            cur.execute(
                "UPDATE opening SET qty=?, unit_cost=? WHERE product_code=?",
                (float(r["qty"] or 0), float(r["unit_cost"] or 0), r["code"])
            )
        c.commit()

def get_opening_cash() -> float:
    with closing(conn()) as c:
        row = c.execute("SELECT amount FROM opening_cash WHERE id=1").fetchone()
        return float(row[0]) if row else 0.0

def save_opening_cash(amount: float):
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("UPDATE opening_cash SET amount=? WHERE id=1", (float(amount),))
        c.commit()

# alış/satış
def add_txn(tdate: date, ttype: str, product_code: str, qty: float, unit_price: float, note: str = ""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("""INSERT INTO transactions(ts,tdate,ttype,product_code,qty,unit_price,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (ts, tdate.isoformat(), ttype, product_code, qty, unit_price, note))
        c.commit()

def get_txns(limit: int = 500) -> pd.DataFrame:
    with closing(conn()) as c:
        q = """SELECT id, ts, tdate, ttype, product_code, qty, unit_price, note
               FROM transactions
               ORDER BY ts DESC, id DESC
               LIMIT ?"""
        return pd.read_sql_query(q, c, params=(limit,))

def delete_txn(row_id: int):
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("DELETE FROM transactions WHERE id=?", (row_id,))
        c.commit()

# nakit hareket (tahsilat/ödeme)
def add_cash_move(tdate: date, mtype: str, person: str, amount: float, note: str = ""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("""INSERT INTO cash_moves(ts,tdate,mtype,person,amount,note)
                       VALUES(?,?,?,?,?,?)""",
                    (ts, tdate.isoformat(), mtype, person.strip(), amount, note))
        c.commit()

def get_cash_moves(limit: int = 300) -> pd.DataFrame:
    with closing(conn()) as c:
        q = """SELECT id, ts, tdate, mtype, person, amount, note
               FROM cash_moves
               ORDER BY ts DESC, id DESC
               LIMIT ?"""
        return pd.read_sql_query(q, c, params=(limit,))

def delete_cash_move(row_id: int):
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("DELETE FROM cash_moves WHERE id=?", (row_id,))
        c.commit()

# gram bazlı borç/alacak
def add_liability(tdate: date, side: str, person: str, product_code: str, grams: float, note: str=""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("""INSERT INTO liabilities(ts,tdate,side,person,product_code,grams,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (ts, tdate.isoformat(), side, person.strip(), product_code, grams, note))
        c.commit()

def get_liabilities(limit: int = 500) -> pd.DataFrame:
    with closing(conn()) as c:
        q = """SELECT id, ts, tdate, side, person, product_code, grams, note
               FROM liabilities
               ORDER BY ts DESC, id DESC
               LIMIT ?"""
        return pd.read_sql_query(q, c, params=(limit,))

def delete_liability(row_id: int):
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("DELETE FROM liabilities WHERE id=?", (row_id,))
        c.commit()

# =============== HESAPLAMA ===============
def to_float(x) -> float:
    if x is None: return 0.0
    if isinstance(x, (int, float)): return float(x)
    s = str(x).strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def chronological_txns() -> pd.DataFrame:
    with closing(conn()) as c:
        q = """SELECT ts, tdate, ttype, product_code, qty, unit_price
               FROM transactions
               ORDER BY ts ASC, id ASC"""
        return pd.read_sql_query(q, c)

def running_avg_pnl() -> Tuple[pd.DataFrame, pd.DataFrame]:
    prods = df_products().set_index("code")[["name", "unit"]]
    opening = get_opening().set_index("code")[["qty", "unit_cost"]]

    state_qty: Dict[str, float] = {code: float(opening.loc[code, "qty"]) if code in opening.index else 0.0
                                   for code in prods.index}
    state_cost: Dict[str, float] = {code: float(opening.loc[code, "unit_cost"]) if code in opening.index else 0.0
                                    for code in prods.index}

    pnl_rows, inv_rows = [], []
    tx = chronological_txns()
    if tx.empty:
        for code in prods.index:
            q = state_qty[code]; ac = state_cost[code]
            inv_rows.append([code, prods.loc[code,"name"], prods.loc[code,"unit"], q, ac, q*ac])
        pnl_df = pd.DataFrame(columns=["tarih","toplam_alış","toplam_satış","günlük_kâr"])
        inv_df = pd.DataFrame(inv_rows, columns=["code","ürün","birim","kalan_miktar","ort_maliyet","envanter_değeri"])
        return pnl_df, inv_df

    current_day = None
    day_purchases = day_sales = day_profit = 0.0

    for _, r in tx.iterrows():
        d = r["tdate"]
        if current_day is None:
            current_day = d
        if d != current_day:
            pnl_rows.append([current_day, day_purchases, day_sales, day_profit])
            current_day = d
            day_purchases = day_sales = day_profit = 0.0

        code = r["product_code"]
        qty = to_float(r["qty"])
        price = to_float(r["unit_price"])

        if r["ttype"] == "BUY":
            old_q, old_c = state_qty[code], state_cost[code]
            new_q = old_q + qty
            if new_q <= 0:
                state_qty[code] = 0.0
            else:
                new_cost = (old_q * old_c + qty * price) / new_q
                state_cost[code] = new_cost
                state_qty[code] = new_q
            day_purchases += qty * price
        else:  # SELL
            ac = state_cost[code]
            state_qty[code] = max(0.0, state_qty[code] - qty)
            sale_total = qty * price
            cogs = qty * ac
            day_sales += sale_total
            day_profit += (sale_total - cogs)

    if current_day is not None:
        pnl_rows.append([current_day, day_purchases, day_sales, day_profit])

    for code in prods.index:
        q = state_qty[code]; ac = state_cost[code]
        inv_rows.append([code, prods.loc[code,"name"], prods.loc[code,"unit"], q, ac, q*ac])

    pnl_df = pd.DataFrame(pnl_rows, columns=["tarih","toplam_alış","toplam_satış","günlük_kâr"])
    pnl_df["tarih"] = pd.to_datetime(pnl_df["tarih"]).dt.date
    pnl_df["kümülatif_kâr"] = pnl_df["günlük_kâr"].cumsum()

    inv_df = pd.DataFrame(inv_rows, columns=["code","ürün","birim","kalan_miktar","ort_maliyet","envanter_değeri"])
    inv_df = inv_df.sort_values("ürün")
    return pnl_df, inv_df

def cash_balance(pnl_df: pd.DataFrame) -> float:
    opening_cash = get_opening_cash()
    flow_tx = float(pnl_df["toplam_satış"].sum() - pnl_df["toplam_alış"].sum()) if not pnl_df.empty else 0.0
    # tahsilat/ödeme
    with closing(conn()) as c:
        cm = pd.read_sql_query("""SELECT mtype, amount FROM cash_moves""", c)
    if cm.empty:
        flow_cm = 0.0
    else:
        cm["signed"] = cm["amount"] * cm["mtype"].map({"TAHSILAT": +1, "ODEME": -1})
        flow_cm = float(cm["signed"].sum())
    return opening_cash + flow_tx + flow_cm

def liabilities_summary() -> pd.DataFrame:
    """Kişi x ürün bazında net gram (ALACAK +, BORC -)."""
    with closing(conn()) as c:
        li = pd.read_sql_query("""SELECT side, person, product_code, grams FROM liabilities""", c)
        prods = df_products().set_index("code")["name"].to_dict()
    if li.empty:
        return pd.DataFrame(columns=["Kişi","Ürün","Net Gram"])
    li["signed"] = li.apply(lambda r: r["grams"] * (+1 if r["side"]=="ALACAK" else -1), axis=1)
    li["Ürün"] = li["product_code"].map(prods)
    out = li.groupby(["person","Ürün"], as_index=False)["signed"].sum()
    out = out.rename(columns={"person":"Kişi","signed":"Net Gram"}).sort_values(["Kişi","Ürün"])
    return out

def person_card(person: str) -> pd.DataFrame:
    """Seçili kişi için son kayıtlar ve net gram özetini döndürür."""
    with closing(conn()) as c:
        li = pd.read_sql_query("""SELECT tdate, side, product_code, grams, note
                                  FROM liabilities WHERE person=? ORDER BY tdate DESC, rowid DESC""", c, params=(person,))
        cm = pd.read_sql_query("""SELECT tdate, mtype, amount, note
                                  FROM cash_moves WHERE person=? ORDER BY tdate DESC, rowid DESC""", c, params=(person,))
        prods = df_products().set_index("code")["name"].to_dict()
    if not li.empty:
        li["Ürün"] = li["product_code"].map(prods)
        li["Gram (±)"] = li.apply(lambda r: r["grams"]*(+1 if r["side"]=="ALACAK" else -1), axis=1)
    return li, cm

# =============== UI ===============
def header():
    st.markdown("## 💎 Sarıkaya Kuyumculuk — Kâr/Zarar • Envanter • Borç/Alacak")
    st.caption("Alış/Satış, envanter, kasa (TL açılış + satış−alış + tahsilat−ödeme) ve kişi bazlı gram borç/alacak")

def tab_txn():
    st.markdown("### 🧾 İşlem Girişi (Alış / Satış)")
    prods = df_products()
    colA, colB, colC, colD = st.columns([2,1,1,2])
    with st.form("txn_form", clear_on_submit=False):
        with colA:
            product = st.selectbox("Ürün", prods["name"].tolist(), index=4, key="txn_prod")
        with colB:
            ttype = st.radio("Tür", ["Satış", "Alış"], horizontal=True, key="txn_type")
        with colC:
            qty = st.number_input("Miktar", min_value=0.00, step=1.00, value=1.00, format="%.2f", key="txn_qty")
        with colD:
            unit_price = st.text_input("Birim Fiyat (₺)", value="0", key="txn_price")
        tdate = st.date_input("Tarih", value=date.today(), key="txn_date")
        note = st.text_input("Not (opsiyonel)", key="txn_note")
        submitted = st.form_submit_button("Kaydet", use_container_width=True)
    if submitted:
        code = prods.loc[prods["name"] == product, "code"].iloc[0]
        add_txn(tdate, "BUY" if ttype=="Alış" else "SELL", code, to_float(qty), to_float(unit_price), note)
        st.success("İşlem kaydedildi.")

    st.markdown("#### Son İşlemler")
    tx = get_txns(200)
    if tx.empty:
        st.info("Henüz işlem yok.")
    else:
        show = tx.rename(columns={
            "ts":"zaman","tdate":"tarih","ttype":"tür","product_code":"ürün_kodu",
            "qty":"miktar","unit_price":"birim_fiyat","note":"not"
        })
        st.dataframe(show, use_container_width=True, height=300)
        col1, col2 = st.columns(2)
        with col1:
            rid = st.number_input("Silinecek işlem id", min_value=0, step=1, value=0, format="%d")
        with col2:
            if st.button("İşlemi Sil"):
                if rid > 0:
                    delete_txn(int(rid)); st.success("Silindi. Yenileyin.")
                else:
                    st.warning("Geçerli bir ID girin.")

def tab_envanter():
    st.markdown("### 📦 Envanter & Kasa Özeti")
    pnl_df, inv_df = running_avg_pnl()
    kasa = cash_balance(pnl_df)
    col1, col2, col3 = st.columns(3)
    with col1: st.metric("Kasa (TL)", f"{kasa:,.2f}".replace(",", "."))
    with col2: st.metric("Toplam Envanter Değeri (TL)", f"{inv_df['envanter_değeri'].sum():,.2f}".replace(",", "."))
    with col3:
        toplam_kar = pnl_df["günlük_kâr"].sum() if not pnl_df.empty else 0.0
        st.metric("Toplam Kâr (Açılıştan bugüne)", f"{toplam_kar:,.2f}".replace(",", "."))
    st.markdown("#### Envanter Detayı (ağırlıklı ort. maliyet)")
    inv_show = inv_df.rename(columns={
        "ürün":"Ürün","birim":"Birim","kalan_miktar":"Kalan",
        "ort_maliyet":"Ort. Maliyet","envanter_değeri":"Değer (TL)"
    })[["Ürün","Birim","Kalan","Ort. Maliyet","Değer (TL)"]]
    st.dataframe(inv_show, use_container_width=True, height=340)

def tab_pnl():
    st.markdown("### 📈 Günlük Kâr / Zarar")
    pnl_df, _ = running_avg_pnl()
    if pnl_df.empty:
        st.info("Henüz işlem yok.")
        return
    show = pnl_df.rename(columns={
        "tarih":"Tarih","toplam_alış":"Toplam Alış",
        "toplam_satış":"Toplam Satış","günlük_kâr":"Günlük Kâr",
        "kümülatif_kâr":"Kümülatif Kâr"
    })
    st.dataframe(show, use_container_width=True, height=340)

def tab_opening():
    st.markdown("### 🧰 Açılış Stokları & TL Açılış")
    # TL Açılış
    st.markdown("#### TL Açılış Bakiyesi")
    current_cash = get_opening_cash()
    colA, colB = st.columns([2,1])
    with colA:
        cash_in = st.text_input("TL Açılış (₺)", value=f"{current_cash:.2f}", key="op_cash")
    with colB:
        if st.button("TL Açılışı Kaydet", use_container_width=True):
            save_opening_cash(to_float(cash_in)); st.success("Güncellendi.")
    st.divider()
    # Ürün Açılış
    st.markdown("#### Ürün Açılışları (Miktar & Birim Maliyet)")
    df = get_opening()[["code","name","unit","qty","unit_cost"]].rename(
        columns={"code":"code","name":"ürün","unit":"birim","qty":"qty","unit_cost":"unit_cost"})
    st.caption("Virgül veya nokta kullanabilirsiniz; otomatik düzeltilir.")
    edit = st.data_editor(
        df, use_container_width=True, num_rows="fixed", hide_index=True,
        column_config={
            "code": st.column_config.TextColumn("Kod", disabled=True),
            "ürün": st.column_config.TextColumn("Ürün", disabled=True),
            "birim": st.column_config.TextColumn("Birim", disabled=True),
            "qty": st.column_config.NumberColumn("Miktar", step=0.01, format="%.3f"),
            "unit_cost": st.column_config.NumberColumn("Birim Maliyet (TL)", step=0.01, format="%.2f"),
        }, key="opening_editor", height=360
    )
    if st.button("Açılış Stoklarını Kaydet", use_container_width=True):
        tmp = edit.copy()
        tmp["qty"] = tmp["qty"].map(to_float); tmp["unit_cost"] = tmp["unit_cost"].map(to_float)
        save_opening(tmp.rename(columns={"ürün":"name","birim":"unit"}))
        st.success("Açılış stokları güncellendi.")

def tab_cash_and_liabilities():
    st.markdown("### 🤝 Tahsilat / Ödeme (TL) & Borç/Alacak (Gram)")

    prods = df_products()
    col1, col2 = st.columns(2)

    # ---- NAKİT: Tahsilat/Ödeme ----
    with col1:
        st.subheader("💵 Tahsilat / Ödeme (Kasa)")
        with st.form("cash_form", clear_on_submit=False):
            mtype = st.radio("Tür", ["Tahsilat (Kasa +)", "Ödeme (Kasa -)"], horizontal=False, key="cm_type")
            person = st.text_input("İsim Soyisim (opsiyonel)", key="cm_person")
            amount = st.text_input("Tutar (₺)", value="0", key="cm_amount")
            mdate  = st.date_input("Tarih", value=date.today(), key="cm_date")
            note   = st.text_input("Not", key="cm_note")
            ok = st.form_submit_button("Kaydet", use_container_width=True)
        if ok:
            kind = "TAHSILAT" if "Tahsilat" in mtype else "ODEME"
            val = to_float(amount)
            if val <= 0:
                st.error("Tutar 0’dan büyük olmalı.")
            else:
                add_cash_move(mdate, kind, person, val, note)
                st.success("Kayıt eklendi.")

        st.markdown("#### Son Nakit Hareketleri")
        cm = get_cash_moves(200)
        if cm.empty:
            st.info("Kayıt yok.")
        else:
            st.dataframe(cm.rename(columns={"tdate":"tarih","mtype":"tür","person":"kişi","amount":"tutar"}), use_container_width=True, height=260)
            rid = st.number_input("Silinecek nakit kayıt id", min_value=0, step=1, value=0, format="%d")
            if st.button("Nakit Kaydı Sil"):
                if rid>0: delete_cash_move(int(rid)); st.success("Silindi. Yenileyin.")
                else: st.warning("Geçerli ID girin.")

    # ---- GRAM: Borç/Alacak ----
    with col2:
        st.subheader("⚖️ Borç / Alacak (Gram Bazlı)")
        with st.form("li_form", clear_on_submit=False):
            side = st.radio("Taraf", ["Alacak (Bize borçlu)", "Borç (Biz borçluyuz)"], horizontal=False, key="li_side")
            person = st.text_input("İsim Soyisim", key="li_person")
            product = st.selectbox("Ürün", prods["name"].tolist(), index=4, key="li_prod")
            grams = st.text_input("Gram/Adet", value="1", key="li_grams")
            ldate = st.date_input("Tarih", value=date.today(), key="li_date")
            note  = st.text_input("Not", key="li_note")
            ok2 = st.form_submit_button("Kaydet", use_container_width=True)
        if ok2:
            gval = to_float(grams)
            if not person.strip():
                st.error("İsim Soyisim zorunlu.")
            elif gval <= 0:
                st.error("Gram/Adet 0’dan büyük olmalı.")
            else:
                code = prods.loc[prods["name"]==product, "code"].iloc[0]
                tag = "ALACAK" if side.startswith("Alacak") else "BORC"
                add_liability(ldate, tag, person.strip(), code, gval, note)
                st.success("Kayıt eklendi.")

        st.markdown("#### Kişi Bazlı Gram Özeti")
        li_sum = liabilities_summary()
        st.dataframe(li_sum, use_container_width=True, height=220)

        st.markdown("#### Son Borç/Alacak Kayıtları")
        li = get_liabilities(200)
        if li.empty:
            st.info("Kayıt yok.")
        else:
            show = li.rename(columns={
                "tdate":"tarih","side":"taraf","person":"kişi",
                "product_code":"ürün_kodu","grams":"gram","note":"not"
            })
            st.dataframe(show, use_container_width=True, height=220)
            rid2 = st.number_input("Silinecek borç/alacak id", min_value=0, step=1, value=0, format="%d")
            if st.button("Borç/Alacak Kaydı Sil"):
                if rid2>0: delete_liability(int(rid2)); st.success("Silindi. Yenileyin.")
                else: st.warning("Geçerli ID girin.")

    # ---- Kişi kartı (hızlı kontrol) ----
    st.markdown("### 👤 Kişi Özeti")
    who = st.text_input("Kişi adı (tam yaz):", key="person_lookup")
    if who.strip():
        li, cm = person_card(who.strip())
        colx, coly = st.columns(2)
        with colx:
            st.markdown("**Gram Hareketleri (Borç/Alacak)**")
            if li.empty: st.info("Kayıt yok.")
            else: st.dataframe(li[["tdate","side","Ürün","grams","Gram (±)","note"]].rename(
                columns={"tdate":"tarih","side":"taraf","grams":"gram","note":"not"}), use_container_width=True, height=220)
        with coly:
            st.markdown("**TL Hareketleri (Tahsilat/Ödeme)**")
            if cm.empty: st.info("Kayıt yok.")
            else: st.dataframe(cm.rename(columns={"tdate":"tarih","mtype":"tür","amount":"tutar","note":"not"}), use_container_width=True, height=220)

# =============== ANA ===============
def main():
    ensure_db()
    header()
    t1, t2, t3, t4, t5 = st.tabs([
        "🧾 Alış / Satış",
        "📦 Envanter & Kasa",
        "📈 Kâr/Zarar",
        "🧰 Açılış Stokları",
        "🤝 Tahsilat/Ödeme & Borç/Alacak"
    ])
    with t1: tab_txn()
    with t2: tab_envanter()
    with t3: tab_pnl()
    with t4: tab_opening()
    with t5: tab_cash_and_liabilities()

if __name__ == "__main__":
    main()