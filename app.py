# app.py
import io
import sqlite3
import datetime as dt
from typing import Optional

import pandas as pd
import streamlit as st

DB = "data.db"

# ---------- yardımcılar ----------
def conn():
    return sqlite3.connect(DB, check_same_thread=False)

def tr_to_float(x) -> Optional[float]:
    """Türkçe sayı -> float. '5.924,87' -> 5924.87  | '0,3520' -> 0.3520"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    # Boşlukları ve TL, ₺ gibi ekleri temizle
    for bad in ["₺", "TL", "tl", " ", "\u00a0"]:
        s = s.replace(bad, "")
    # Binlik ayıracı olan noktaları sil, virgülü noktaya çevir
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def init_db():
    with conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS prices(
            source TEXT,
            name   TEXT,
            buy    REAL,
            sell   REAL,
            ts     TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS margins(
            product TEXT PRIMARY KEY,
            buy_adj REAL DEFAULT 0,   -- alışta düşülecek TL
            sell_adj REAL DEFAULT 0   -- satışta eklenecek TL
        )
        """)
        # Varsayılan ürünler ve marjlar
        defaults = [
            ("Çeyrek Altın", 0,   50),
            ("Yarım Altın",   0,  100),
            ("Tam Altın",     0,  200),
            ("Ata Lira",      0,  200),
            ("24 Ayar Gram", 20,   10),   # Gram: alış -20, satış +10
        ]
        for p, b, s in defaults:
            c.execute("INSERT OR IGNORE INTO margins(product,buy_adj,sell_adj) VALUES(?,?,?)",
                      (p, b, s))

def write_prices(source: str, df: pd.DataFrame):
    now = dt.datetime.utcnow().isoformat()
    out = []
    for _, r in df.iterrows():
        out.append((
            source,
            str(r["name"]).strip(),
            tr_to_float(r.get("buy")),
            tr_to_float(r.get("sell")),
            now
        ))
    with conn() as c:
        c.executemany("INSERT INTO prices(source,name,buy,sell,ts) VALUES(?,?,?,?,?)", out)

def read_prices(src: Optional[str] = None) -> pd.DataFrame:
    q = "SELECT source,name,buy,sell,ts FROM prices"
    args = ()
    if src:
        q += " WHERE source=?"
        args = (src,)
    q += " ORDER BY ts DESC"
    return pd.read_sql_query(q, conn(), params=args)

# Harem adlılarını esnek eşle
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım", "Yarım"],
    "Tam Altın":    ["Eski Tam", "Tam"],
    "Ata Lira":     ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram"],
}

def last_harem_sell(product: str) -> Optional[float]:
    aliases = HAREM_ALIASES.get(product, [product])
    placeholders = ",".join(["?"] * len(aliases))
    q = f"""
        SELECT sell FROM prices
        WHERE source='HAREM' AND name IN ({placeholders})
        ORDER BY ts DESC
        LIMIT 1
    """
    with conn() as c:
        row = c.execute(q, aliases).fetchone()
    return tr_to_float(row[0]) if row else None

def suggestion(product: str, ttype: str) -> Optional[float]:
    base = last_harem_sell(product)
    if base is None:
        return None
    m = pd.read_sql_query("SELECT * FROM margins WHERE product=?",
                          conn(), params=(product,))
    buy_adj = float(m.iloc[0]["buy_adj"]) if not m.empty else 0.0
    sell_adj = float(m.iloc[0]["sell_adj"]) if not m.empty else 0.0
    if ttype == "Alış":
        return round(base - buy_adj, 2)
    else:
        return round(base + sell_adj, 2)

# ---------- UI ----------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")
init_db()

st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

tab_harem, tab_islem, tab_ozbag, tab_marg = st.tabs([
    "Harem Fiyatları (Müşteri Bazı)",
    "İşlem (Alış/Satış)",
    "Özbağ Fiyatları (Has Referansı)",
    "Marj Ayarları",
])

with tab_harem:
    st.subheader("Harem CSV içe al")
    st.caption("CSV biçimi: **Ad,Alış,Satış**  | Örnek satırlar: "
               "`Eski Çeyrek,9516,9644`  `Gram Altın,5836,65,5924,87`")
    h_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_in")
    if st.button("Harem İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(h_txt), header=None, names=["name","buy","sell"])
            write_prices("HAREM", df)
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.subheader("Son Harem Kayıtları")
    st.data_editor(read_prices("HAREM"), use_container_width=True)

with tab_ozbag:
    st.subheader("Özbağ CSV içe al (Has çarpanı referansı)")
    st.caption("CSV biçimi: **Ad,Has**  | Örnek: `Çeyrek,0,3520`  `24 Ayar Gram,1,0000`")
    o_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="ozbag_in")
    if st.button("Özbağ İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(o_txt), header=None, names=["name","sell"])
            df["buy"] = None  # kullanılmıyor; kolon boş bırakıyoruz
            write_prices("OZBAG", df[["name","buy","sell"]])
            st.success("Özbağ referansları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.subheader("Son Özbağ Kayıtları")
    st.data_editor(read_prices("OZBAG"), use_container_width=True)

with tab_marg:
    st.subheader("Marj Ayarları")
    st.caption("Alış: Harem satış − **buy_adj** | Satış: Harem satış + **sell_adj** (TL)")
    mdf = pd.read_sql_query("SELECT * FROM margins", conn())
    new = st.data_editor(mdf, num_rows="dynamic", use_container_width=True, key="marg_editor")
    if st.button("Kaydet (Marj)"):
        with conn() as c:
            c.execute("DELETE FROM margins")
            c.executemany("INSERT INTO margins(product,buy_adj,sell_adj) VALUES(?,?,?)",
                          list(new[["product","buy_adj","sell_adj"]].itertuples(index=False)))
        st.success("Marjlar güncellendi.")

with tab_islem:
    st.subheader("İşlem (Alış/Satış)")
    st.caption("Öneri, Harem’deki **son satış** satırından hesaplanır.")
    products = ["Çeyrek Altın","Yarım Altın","Tam Altın","Ata Lira","24 Ayar Gram"]
    col1, col2 = st.columns([1,1])
    with col1:
        product = st.selectbox("Ürün", products)
        ttype = st.radio("Tür", ["Satış","Alış"], horizontal=True)
    with col2:
        qty = st.number_input("Gram / Adet", min_value=1.0, value=1.0, step=1.0)

    base = last_harem_sell(product)
    sug  = suggestion(product, ttype)

    box = st.container(border=True)
    with box:
        if base is None:
            st.error("Harem’de uygun satır bulunamadı. Harem CSV’si ekleyin.")
        else:
            st.write(f"**Harem son satış**: {base:,.2f} ₺")
            if ttype == "Alış":
                adj = pd.read_sql_query("SELECT buy_adj FROM margins WHERE product=?",
                                        conn(), params=(product,))
                adjv = float(adj.iloc[0]["buy_adj"]) if not adj.empty else 0.0
                st.write(f"Formül: {base:,.2f} − {adjv:,.2f}")
            else:
                adj = pd.read_sql_query("SELECT sell_adj FROM margins WHERE product=?",
                                        conn(), params=(product,))
                adjv = float(adj.iloc[0]["sell_adj"]) if not adj.empty else 0.0
                st.write(f"Formül: {base:,.2f} + {adjv:,.2f}")

            if sug is not None:
                st.subheader(f"Öneri: {sug:,.2f} ₺")
                st.caption("İşlem kaydı veritabanına yazmıyoruz; bu panel fiyat onayı içindir.")
            else:
                st.error("Öneri hesaplanamadı.")

    with st.expander("🔎 Fiyat çekim debug"):
        st.json({
            "product": product,
            "ttype": ttype,
            "base_sell": base,
            "matched_aliases": HAREM_ALIASES.get(product, [product]),
            "now": dt.datetime.utcnow().isoformat()
        })