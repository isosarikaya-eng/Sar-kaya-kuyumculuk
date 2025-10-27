import sqlite3
from contextlib import closing
from datetime import datetime, date
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

# ===================== GENEL =====================
st.set_page_config(page_title="Sarıkaya Kuyumculuk — Kâr/Zarar & Envanter", layout="wide")
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

# ===================== DB =====================
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
        )
        """)
        # açılış stokları
        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening(
          product_code TEXT PRIMARY KEY,
          qty REAL NOT NULL DEFAULT 0,
          unit_cost REAL NOT NULL DEFAULT 0,
          FOREIGN KEY(product_code) REFERENCES products(code)
        )
        """)
        # açılış TL (tek satır)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening_cash(
          id INTEGER PRIMARY KEY CHECK(id=1),
          amount REAL NOT NULL DEFAULT 0
        )
        """)
        cur.execute("INSERT OR IGNORE INTO opening_cash(id, amount) VALUES(1, 0)")

        # işlemler
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,         -- ISO datetime
          tdate TEXT NOT NULL,      -- YYYY-MM-DD
          ttype TEXT NOT NULL,      -- BUY / SELL
          product_code TEXT NOT NULL,
          qty REAL NOT NULL,
          unit_price REAL NOT NULL,
          note TEXT,
          FOREIGN KEY(product_code) REFERENCES products(code)
        )
        """)
        # seed ürünler
        cur.execute("SELECT COUNT(1) FROM products")
        if cur.fetchone()[0] == 0:
            cur.executemany("INSERT INTO products(code,name,unit) VALUES(?,?,?)", PRODUCTS)
        # opening satırları
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
        ORDER BY p.rowid
        """
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

def add_txn(tdate: date, ttype: str, product_code: str, qty: float, unit_price: float, note: str = ""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("""
            INSERT INTO transactions(ts,tdate,ttype,product_code,qty,unit_price,note)
            VALUES(?,?,?,?,?,?,?)
        """, (ts, tdate.isoformat(), ttype, product_code, qty, unit_price, note))
        c.commit()

def get_txns(limit: int = 500) -> pd.DataFrame:
    with closing(conn()) as c:
        q = """
        SELECT id, ts, tdate, ttype, product_code, qty, unit_price, note
        FROM transactions
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """
        return pd.read_sql_query(q, c, params=(limit,))

def delete_txn(row_id: int):
    with closing(conn()) as c, c, closing(c.cursor()) as cur:
        cur.execute("DELETE FROM transactions WHERE id=?", (row_id,))
        c.commit()

# ===================== HESAPLAMA =====================
def to_float(x) -> float:
    if x is None: return 0.0
    if isinstance(x, (int, float)): return float(x)
    s = str(x).strip()
    # 1.234,56 -> 1234.56 ; 1,234.56 -> 1234.56
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # 1.234,56
        else:
            s = s.replace(",", "")                    # 1,234.56
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def chronological_txns() -> pd.DataFrame:
    with closing(conn()) as c:
        q = """
        SELECT ts, tdate, ttype, product_code, qty, unit_price
        FROM transactions
        ORDER BY ts ASC, id ASC
        """
        return pd.read_sql_query(q, c)

def running_avg_pnl() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Satışta kâr = (satış_birim_fiyat - o andaki ağırlıklı ort. maliyet) * qty
    Envanter: açılış + alış - satış; ort. maliyet ağırlıklı ortalama.
    """
    prods = df_products().set_index("code")[["name", "unit"]]
    opening = get_opening().set_index("code")[["qty", "unit_cost"]]

    # state
    state_qty: Dict[str, float] = {code: float(opening.loc[code, "qty"]) if code in opening.index else 0.0
                                   for code in prods.index}
    state_cost: Dict[str, float] = {code: float(opening.loc[code, "unit_cost"]) if code in opening.index else 0.0
                                    for code in prods.index}

    pnl_rows = []   # tarih, alış_tutarı, satış_tutarı, günlük_kâr
    inv_rows = []   # product, qty, avg_cost, value

    tx = chronological_txns()
    if tx.empty:
        # sadece açılış envanteri
        for code in prods.index:
            q = state_qty[code]
            ac = state_cost[code]
            inv_rows.append([code, prods.loc[code,"name"], prods.loc[code,"unit"], q, ac, q*ac])
        pnl_df = pd.DataFrame(columns=["tarih","toplam_alış","toplam_satış","günlük_kâr"])
        inv_df = pd.DataFrame(inv_rows, columns=["code","ürün","birim","kalan_miktar","ort_maliyet","envanter_değeri"])
        return pnl_df, inv_df

    current_day = None
    day_purchases = 0.0
    day_sales = 0.0
    day_profit = 0.0

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

    # son gün ekle
    if current_day is not None:
        pnl_rows.append([current_day, day_purchases, day_sales, day_profit])

    # envanter tablosu
    for code in prods.index:
        q = state_qty[code]
        ac = state_cost[code]
        inv_rows.append([code, prods.loc[code,"name"], prods.loc[code,"unit"], q, ac, q*ac])

    pnl_df = pd.DataFrame(pnl_rows, columns=["tarih","toplam_alış","toplam_satış","günlük_kâr"])
    pnl_df["tarih"] = pd.to_datetime(pnl_df["tarih"]).dt.date
    pnl_df["kümülatif_kâr"] = pnl_df["günlük_kâr"].cumsum()

    inv_df = pd.DataFrame(inv_rows, columns=["code","ürün","birim","kalan_miktar","ort_maliyet","envanter_değeri"])
    inv_df = inv_df.sort_values("ürün")
    return pnl_df, inv_df

def cash_balance(pnl_df: pd.DataFrame) -> float:
    opening_cash = get_opening_cash()
    flow = float(pnl_df["toplam_satış"].sum() - pnl_df["toplam_alış"].sum()) if not pnl_df.empty else 0.0
    return opening_cash + flow

# ===================== UI BÖLÜMLERİ =====================
def header():
    st.markdown("## 💎 Sarıkaya Kuyumculuk — Kâr/Zarar & Envanter")
    st.caption("Sade panel: Alış/Satış kayıt, günlük kâr hesap, envanter özeti. (TL açılış bakiyesi destekli)")

def tab_islem():
    st.markdown("### 🧾 İşlem Girişi (Alış / Satış)")
    prods = df_products()
    colA, colB, colC, colD = st.columns([2, 1, 1, 2])

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
        qtyf = to_float(qty)
        pricef = to_float(unit_price)
        add_txn(tdate, "BUY" if ttype == "Alış" else "SELL", code, qtyf, pricef, note)
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
                    delete_txn(int(rid))
                    st.success("Silindi. Yenilemek için sayfayı tazele.")
                else:
                    st.warning("Geçerli bir ID gir.")

def tab_envanter():
    st.markdown("### 📦 Envanter & Kasa Özeti")
    pnl_df, inv_df = running_avg_pnl()
    kasa = cash_balance(pnl_df)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Kasa (TL)", f"{kasa:,.2f}".replace(",", "."))
    with col2:
        st.metric("Toplam Envanter Değeri (TL)", f"{inv_df['envanter_değeri'].sum():,.2f}".replace(",", "."))
    with col3:
        toplam_kar = pnl_df["günlük_kâr"].sum() if not pnl_df.empty else 0.0
        st.metric("Toplam Kâr (Açılıştan bugüne)", f"{toplam_kar:,.2f}".replace(",", "."))

    st.markdown("#### Envanter Detayı (ağırlıklı ort. maliyet)")
    inv_show = inv_df.rename(columns={
        "ürün": "Ürün", "birim": "Birim", "kalan_miktar": "Kalan",
        "ort_maliyet": "Ort. Maliyet", "envanter_değeri": "Değer (TL)"
    })[["Ürün", "Birim", "Kalan", "Ort. Maliyet", "Değer (TL)"]]
    st.dataframe(inv_show, use_container_width=True, height=340)

def tab_kar_zarar():
    st.markdown("### 📈 Günlük Kâr / Zarar")
    pnl_df, _ = running_avg_pnl()
    if pnl_df.empty:
        st.info("Henüz işlem yok.")
        return
    show = pnl_df.rename(columns={
        "tarih":"Tarih","toplam_alış":"Toplam Alış","toplam_satış":"Toplam Satış",
        "günlük_kâr":"Günlük Kâr","kümülatif_kâr":"Kümülatif Kâr"
    })
    st.dataframe(show, use_container_width=True, height=340)

def tab_acilis():
    st.markdown("### 🧰 Açılış Stokları & TL Açılış Bakiyesi")

    # TL Açılış
    st.markdown("#### TL Açılış Bakiyesi")
    current_cash = get_opening_cash()
    colA, colB = st.columns([2,1])
    with colA:
        cash_in = st.text_input("TL Açılış (₺)", value=f"{current_cash:.2f}", key="op_cash")
    with colB:
        if st.button("TL Açılışı Kaydet", use_container_width=True):
            save_opening_cash(to_float(cash_in))
            st.success("TL açılış bakiyesi güncellendi.")

    st.divider()
    # Envanter Açılış
    st.markdown("#### Ürün Açılışları (Miktar & Birim Maliyet)")
    df = get_opening()[["code","name","unit","qty","unit_cost"]].rename(
        columns={"code":"code","name":"ürün","unit":"birim","qty":"qty","unit_cost":"unit_cost"}
    )
    st.caption("İpucu: Virgül veya nokta kullanabilirsiniz; sistem otomatik düzeltir.")
    edit = st.data_editor(
        df,
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        column_config={
            "code": st.column_config.TextColumn("Kod", disabled=True),
            "ürün": st.column_config.TextColumn("Ürün", disabled=True),
            "birim": st.column_config.TextColumn("Birim", disabled=True),
            "qty": st.column_config.NumberColumn("Miktar", step=0.01, format="%.3f"),
            "unit_cost": st.column_config.NumberColumn("Birim Maliyet (TL)", step=0.01, format="%.2f"),
        },
        key="opening_editor",
        height=360
    )
    if st.button("Açılış Stoklarını Kaydet", use_container_width=True):
        tmp = edit.copy()
        tmp["qty"] = tmp["qty"].map(to_float)
        tmp["unit_cost"] = tmp["unit_cost"].map(to_float)
        save_opening(tmp.rename(columns={"ürün":"name","birim":"unit"}))
        st.success("Açılış stokları güncellendi.")

# ===================== ANA =====================
def main():
    ensure_db()
    st.markdown("## 💎 Sarıkaya Kuyumculuk — Kâr/Zarar & Envanter")
    st.caption("Sade panel: Alış/Satış kayıt, günlük kâr hesap, envanter özeti. (TL açılış bakiyesi destekli)")
    t1, t2, t3, t4 = st.tabs(["🧾 Alış / Satış", "📦 Envanter & Kasa", "📈 Kâr/Zarar", "🧰 Açılış Stokları"])
    with t1: tab_islem()
    with t2: tab_envanter()
    with t3: tab_kar_zarar()
    with t4: tab_acilis()

if __name__ == "__main__":
    main()