# app.py – Alış/Çıkış (Stok İşlemi) paneli dâhil tam iskelet
import io, datetime as dt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")

# -------------------- DB & yardımcılar --------------------
ENGINE = create_engine("sqlite:///data.db", future=True)

def ensure_tables():
    with ENGINE.begin() as conn:
        # fiyat kayıtları
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS prices(
            source TEXT,            -- 'HAREM' / 'OZBAG' / ...
            name   TEXT,            -- ürün adı (Eski Çeyrek, Gram Altın, 24 Ayar Gram ...)
            buy    REAL,
            sell   REAL,
            ts     TEXT
        )"""))
        # işlemler (alış/çıkış)
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,              -- YYYY-MM-DD
            product TEXT,           -- Çeyrek Altın, Yarım Altın, ...
            ttype TEXT,             -- 'Alış' veya 'Çıkış'
            unit  TEXT,             -- 'adet' veya 'gram'
            qty_or_gram REAL,       -- miktar
            unit_price REAL,        -- TL
            total REAL,             -- TL
            note TEXT,
            ts   TEXT               -- kayıt zamanı
        )"""))

ensure_tables()

# Ürün kataloğu (birimini & standart gramı envanter için biliriz)
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995},
    # İstersen 22 ayar/0.5g/0.25g buraya ekleyebilirsin
}

# Harem ad eşleşmeleri (Harem tablosunda nasıl geçtiğini buraya yazıyoruz)
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım", "Yarım"],
    "Tam Altın"   : ["Eski Tam", "Tam"],
    "Ata Lira"    : ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram"],
}

# Marj kuralları (TL)
MARGINS = {
    "24 Ayar Gram": {"buy_minus": 20, "sell_plus": 10},  # alış = harem_satis-20, satış = harem_satis+10
    # diğer ürünlerde satış/alış önerisi doğrudan harem satışı (çoğu adetli üründe piyasada tek fiyat gibi davranırız)
    "default": {"buy_minus": 0, "sell_plus": 0},
}

def read_prices_df(source="HAREM") -> pd.DataFrame:
    with ENGINE.begin() as conn:
        df = pd.read_sql(text("SELECT * FROM prices WHERE source=:s ORDER BY ts DESC"),
                         conn, params={"s": source})
    # düzgün sıralama için ts’yi datetime yap
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        df = df.sort_values("ts", ascending=False)
    return df

def get_harem_sell(product_name: str) -> tuple[float|None, str|None]:
    """Harem tablosunda alias’lara göre son satış değerini bulur."""
    df = read_prices_df("HAREM")
    if df.empty:
        return None, None
    aliases = HAREM_ALIASES.get(product_name, [product_name])
    for alias in aliases:
        m = df[df["name"].str.strip().str.lower() == alias.lower()]
        if not m.empty and pd.notna(m.iloc[0]["sell"]):
            return float(m.iloc[0]["sell"]), alias
    return None, None

def suggested_unit_price(product_name: str, ttype: str) -> tuple[float|None, dict]:
    """Öneri fiyatı: Harem son satış satırı + marj kuralı."""
    base_sell, matched = get_harem_sell(product_name)
    debug = {"product": product_name, "ttype": ttype, "base_sell": base_sell, "matched_name": matched}
    if base_sell is None:
        return None, debug
    rule = MARGINS.get(product_name, MARGINS["default"])
    if ttype == "Alış":
        price = base_sell - rule["buy_minus"]
    else:  # Çıkış (Satış)
        price = base_sell + rule["sell_plus"]
    debug["suggested"] = price
    return price, debug

def write_transaction(row: dict):
    with ENGINE.begin() as conn:
        conn.execute(text("""
            INSERT INTO transactions(date, product, ttype, unit, qty_or_gram, unit_price, total, note, ts)
            VALUES(:date, :product, :ttype, :unit, :qty_or_gram, :unit_price, :total, :note, :ts)
        """), row)

def read_transactions(limit=200) -> pd.DataFrame:
    with ENGINE.begin() as conn:
        df = pd.read_sql(text("SELECT * FROM transactions ORDER BY ts DESC LIMIT :lim"),
                         conn, params={"lim": limit})
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    return df

def inventory_summary() -> pd.DataFrame:
    """İşlemlerden adet/gram bazlı stok özeti."""
    tx = read_transactions(limit=999999)
    if tx.empty:
        return pd.DataFrame()
    # Alış=+, Çıkış=-
    tx["signed_qty"] = tx["qty_or_gram"].astype(float) * tx["ttype"].map({"Alış": 1, "Çıkış": -1})
    g = tx.groupby(["product", "unit"], as_index=False)["signed_qty"].sum().rename(columns={"signed_qty": "stok"})
    # TL maliyet/ciro da gösterebiliriz
    money = tx.copy()
    money["signed_tl"] = money["total"] * money["ttype"].map({"Alış": 1, "Çıkış": -1})
    m = money.groupby(["product"], as_index=False)["signed_tl"].sum().rename(columns={"signed_tl": "net_tl"})
    out = g.merge(m, on="product", how="left")
    return out

# -------------------- UI --------------------
st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

tabs = st.tabs(["📊 Harem Fiyatları", "💱 Alış / Çıkış", "🏦 Kasa & Envanter"])

# ------ TAB: Harem Fiyatları (manuel yapıştırmalı) ------
with tabs[0]:
    st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek: **Eski Çeyrek,9516,9644**  veya **Gram Altın,5820,5900**")
    csv_in = st.text_area("CSV'yi buraya yapıştırın", height=120, key="harem_csv_input")
    if st.button("Harem İçeri Al", type="primary", key="btn_harem_import"):
        try:
            df = pd.read_csv(
    io.StringIO(csv_in),
    header=None,
    names=["name", "buy", "sell"],
    sep=",",          # sütun ayırıcı olarak sadece virgül
    thousands=None,   # binlik ayırıcıyı yok say
    decimal="."       # ondalık ayracı nokta olarak al
)
            df["source"] = "HAREM"
            df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
            # sayılara virgül ihtimali
            for c in ["buy", "sell"]:
                df[c] = (df[c].astype(str).str.replace(".", "", regex=False)
                                   .str.replace(",", ".", regex=False)).astype(float)
            with ENGINE.begin() as conn:
                df[["source", "name", "buy", "sell", "ts"]].to_sql("prices", conn, if_exists="append", index=False)
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    last_h = read_prices_df("HAREM")
    st.markdown("#### Son Harem Kayıtları")
    st.dataframe(last_h, use_container_width=True, height=360)

# ------ TAB: Alış/Çıkış (stok işlemi) ------
with tabs[1]:
    st.subheader("Alış / Çıkış İşlemi")
    st.caption("Öneri, Harem'deki **son satış** satırından hesaplanır (marj kuralıyla).")

    colL, colR = st.columns([1,1])
    with colL:
        product = st.selectbox("Ürün Seç", list(PRODUCTS.keys()), key="tx_prod")
    with colR:
        ttype = st.radio("İşlem Türü", ["Alış", "Çıkış"], horizontal=True, key="tx_type")

    unit = PRODUCTS[product]["unit"]
    qty_label = "Adet" if unit == "adet" else "Gram"
    qty = st.number_input(qty_label, min_value=0.01, value=1.00, step=1.00 if unit=="adet" else 0.10, key="tx_qty")

    # öneri fiyat
    suggested, dbg = suggested_unit_price(product, ttype)
    price = st.number_input("Birim Fiyat (TL)", min_value=0.0,
                            value=float(round(suggested,2)) if suggested else 0.0,
                            step=1.0, key="tx_price")
    total = qty * price
    st.markdown(f"### Önerilen Fiyat\n**{total:,.2f} ₺**".replace(",", "X").replace(".", ",").replace("X", "."))  # TR biçim

    # satışta taban kontrolü: harem satış altına düşme
    warn = False
    base_sell, matched = get_harem_sell(product)
    if base_sell is not None and ttype == "Çıkış" and price < base_sell:
        st.warning("⚠️ Satış fiyatı Harem **satış** fiyatının altında olamaz!")
        warn = True

    date = st.date_input("Tarih", value=dt.date.today(), key="tx_date")
    note = st.text_input("Not", key="tx_note")

    if st.button("Kaydet", type="primary", key="tx_save"):
        if qty <= 0 or price <= 0:
            st.error("Miktar ve fiyat sıfırdan büyük olmalı.")
        elif warn:
            st.error("Satış fiyatını Harem satışının altına giremezsiniz.")
        else:
            write_transaction({
                "date": str(date),
                "product": product,
                "ttype": ttype,
                "unit": unit,
                "qty_or_gram": float(qty),
                "unit_price": float(price),
                "total": float(total),
                "note": note,
                "ts": dt.datetime.utcnow().isoformat(timespec="seconds")
            })
            st.success("İşlem kaydedildi.")

    st.markdown("#### Son İşlemler")
    st.dataframe(read_transactions(50), use_container_width=True, height=360)

    with st.expander("🔎 Fiyat çekim debug"):
        st.json(dbg)

# ------ TAB: Kasa & Envanter ------
with tabs[2]:
    st.subheader("Kasa & Envanter")
    inv = inventory_summary()
    if inv.empty:
        st.info("Henüz işlem yok.")
    else:
        st.markdown("#### Envanter Özeti")
        st.dataframe(inv, use_container_width=True, height=360)

        kasa = read_transactions(999999)
        if not kasa.empty:
            kasa["signed"] = kasa["total"] * kasa["ttype"].map({"Alış": -1, "Çıkış": 1})
            balance = kasa["signed"].sum()
        else:
            balance = 0.0
        st.metric("Kasa Bakiyesi (TL)", f"{balance:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X", "."))