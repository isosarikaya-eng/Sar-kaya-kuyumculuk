import streamlit as st
import pandas as pd
import sqlite3, io, datetime as dt
from typing import Optional, Tuple

DB = "data.db"

# ---------- DB ----------
def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS prices(
        source TEXT, name TEXT, buy REAL, sell REAL, ts TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions(
        ts TEXT, product TEXT, ttype TEXT, unit TEXT,
        qty REAL, price REAL, total REAL, note TEXT
    )""")
    return c

def write_prices(df: pd.DataFrame):
    c = conn()
    df = df.copy()
    df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    df["source"] = "HAREM"
    df[["buy","sell"]] = df[["buy","sell"]].astype(float)
    df[["source","name","buy","sell","ts"]].to_sql("prices", c, if_exists="append", index=False)
    c.commit(); c.close()

def read_prices(n: int = 50) -> pd.DataFrame:
    c = conn()
    df = pd.read_sql_query("SELECT * FROM prices ORDER BY ts DESC LIMIT ?", c, params=(n,))
    c.close()
    return df

def write_tx(product, ttype, unit, qty, price, total, note):
    c = conn()
    c.execute(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
        (dt.datetime.utcnow().isoformat(timespec="seconds"),
         product, ttype, unit, qty, price, total, note)
    )
    c.commit(); c.close()

def read_tx() -> pd.DataFrame:
    c = conn()
    df = pd.read_sql_query("SELECT * FROM transactions ORDER BY ts DESC", c)
    c.close()
    return df

# ---------- Yardımcılar ----------
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75, "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50, "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00, "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.0, "purity": 0.995},
}

# Harem ad eşleştirmeleri
HAREM_ALIAS = {
    "Çeyrek Altın": ["Eski Çeyrek"],
    "Yarım Altın" : ["Eski Yarım"],
    "Tam Altın"   : ["Eski Tam"],
    "Ata Lira"    : ["Eski Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "Has"],
}

def parse_number(x: str) -> float:
    """
    '5.924,87' -> 5924.87
    '5924,87'  -> 5924.87
    '5924.87'  -> 5924.87
    """
    x = str(x).strip()
    if "," in x and "." in x:
        # varsayım: . binlik, , ondalık
        x = x.replace(".", "").replace(",", ".")
    elif "," in x and "." not in x:
        x = x.replace(",", ".")
    return float(x)

def parse_harem_csv(txt: str) -> pd.DataFrame:
    rows = []
    for raw in txt.strip().splitlines():
        if not raw.strip():
            continue
        # en çok 3 parça bekliyoruz: name,buy,sell
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Satır hatalı: {raw}")
        name, buy, sell = parts
        rows.append({"name": name, "buy": parse_number(buy), "sell": parse_number(sell)})
    return pd.DataFrame(rows)

def last_harem_price(name_variants: list[str]) -> Optional[Tuple[float, float, str]]:
    df = read_prices(200)
    if df.empty:
        return None
    df = df[df["source"]=="HAREM"]
    for alias in name_variants:
        m = df[df["name"].str.lower()==alias.lower()]
        if not m.empty:
            r = m.iloc[0]
            return float(r["buy"]), float(r["sell"]), r["ts"]
    return None

def suggested(product: str, ttype: str) -> Tuple[Optional[float], dict]:
    if product == "24 Ayar Gram":
        rec = last_harem_price(HAREM_ALIAS[product])
        if not rec:
            return None, {"reason":"Harem 'Gram Altın' bulunamadı"}
        _buy, _sell, ts = rec
        # kural: Alış = Harem satış − 20  | Satış = Harem satış + 10
        base_sell = _sell
        price = base_sell - 20 if ttype=="Alış" else base_sell + 10
        return round(price, 2), {"product":product, "ttype":ttype, "base_sell":base_sell, "ts":ts}
    else:
        rec = last_harem_price(HAREM_ALIAS[product])
        if not rec:
            return None, {"reason":f"Harem '{HAREM_ALIAS[product][0]}' yok"}
        h_buy, h_sell, ts = rec
        price = h_buy if ttype=="Alış" else h_sell
        return round(price, 2), {"product":product, "ttype":ttype, "h_buy":h_buy, "h_sell":h_sell, "ts":ts}

def inventory_summary() -> pd.DataFrame:
    tx = read_tx()
    if tx.empty:
        return pd.DataFrame(columns=["product","unit","qty"])
    g = tx.groupby(["product","unit","ttype"])["qty"].sum().unstack(fill_value=0)
    g["qty"] = g.get("Alış",0) - g.get("Satış",0)
    g = g.reset_index()[["product","unit","qty"]]
    return g

def cash_summary() -> float:
    tx = read_tx()
    if tx.empty:
        return 0.0
    # Satışta para girer (+), alışta çıkar (-)
    tx["flow"] = tx.apply(lambda r: r["total"] if r["ttype"]=="Satış" else -r["total"], axis=1)
    return float(tx["flow"].sum())

# ---------- UI ----------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="centered")
st.title("💎 Sarıkaya Kuyumculuk\n– Entegrasyon")

tabs = st.tabs(["📊 Harem Fiyatları", "💱 Alış / Satış", "🏦 Kasa & Envanter"])

# --- HAREM ---
with tabs[0]:
    st.caption("CSV biçimi: **Ad,Alış,Satış**  • Örnek: `Eski Çeyrek,9516,9644`")
    ta = st.text_area("CSV'yi buraya yapıştırın", height=160, key="harem_csv_input")
    if st.button("Harem İçeri Al"):
        try:
            df = parse_harem_csv(ta)
            write_prices(df)
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")
    st.subheader("Son Harem Kayıtları")
    st.dataframe(read_prices(100), use_container_width=True)

# --- TRADE ---
with tabs[1]:
    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri, Harem’deki **son kayıttan** hesaplanır.")
    col1, col2 = st.columns(2)
    product = col1.selectbox("Ürün Seç", list(PRODUCTS.keys()))
    ttype   = col2.radio("İşlem Türü", ["Alış","Satış"], horizontal=True)

    unit = PRODUCTS[product]["unit"]
    qty = st.number_input("Adet / Gram", min_value=0.01, value=1.00, step=1.0 if unit=="adet" else 0.10)

    sug, debug = suggested(product, ttype)
    if sug is None:
        st.warning("Öneri hesaplanamadı. Önce Harem’e ilgili satırı kaydedin.")
    else:
        st.markdown(f"### Önerilen Fiyat\n**{sug:,.2f} ₺**".replace(",", "X").replace(".", ",").replace("X", "."))

    # Manuel fiyat
    price = st.number_input("Manuel Birim Fiyat (TL)", min_value=0.0, value=float(sug or 0.0), step=1.0)
    total = price * qty
    st.success(f"Toplam: {total:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X", "."))

    # Uyarı: satış < alış
    if product != "24 Ayar Gram":
        # Coins için Harem alış/satış sabit
        c_buy, _ = suggested(product, "Alış")
    else:
        c_buy, _ = suggested("24 Ayar Gram", "Alış")
    if ttype=="Satış" and c_buy is not None and price < c_buy:
        st.error("⚠️ Satış fiyatı **alış fiyatının** altında olamaz!")

    note = st.text_input("Not (opsiyonel)")
    if st.button("Kaydet"):
        if sug is None:
            st.error("Harem fiyatı olmadığı için kayıt yapılamadı.")
        else:
            write_tx(product, ttype, unit, float(qty), float(price), float(total), note)
            st.success("İşlem kaydedildi.")

    with st.expander("🔎 Fiyat çekim debug"):
        st.json(debug)

    st.subheader("Son İşlemler")
    st.dataframe(read_tx(), use_container_width=True)

# --- CASH & INVENTORY ---
with tabs[2]:
    st.subheader("Kasa Özeti")
    tl = cash_summary()
    st.metric("💵 TL Kasa", f"{tl:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X", "."))

    st.subheader("Envanter Özeti")
    inv = inventory_summary()
    st.dataframe(inv, use_container_width=True)