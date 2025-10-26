# app.py
# -*- coding: utf-8 -*-
import io
import datetime as dt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")

DB_URL = "sqlite:///sarikaya_kuyum.db"
engine = create_engine(DB_URL, future=True)

PRICE_COLS = ["source", "name", "buy", "sell", "has", "ts"]

# Harem isim eşleştirmeleri (Gram Altın dahil)
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım", "Yarım"],
    "Tam Altın":    ["Eski Tam", "Tam"],
    "Ata Lira":     ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram", "Has Altın"],
}
# Özbağ (has) isim eşleştirmeleri
OZBAG_ALIASES = {
    "Çeyrek Altın": ["Çeyrek"],
    "Yarım Altın":  ["Yarım"],
    "Tam Altın":    ["Tam"],
    "Ata Lira":     ["Ata"],
    "24 Ayar Gram": ["24 Ayar Gram", "Gram"],
}
PRODUCT_ORDER = ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"]

# ---------- DB yardımcıları ----------
def ensure_tables():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS prices (
            source TEXT,
            name   TEXT,
            buy    REAL,
            sell   REAL,
            has    REAL,
            ts     TEXT
        );
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS transactions (
            ts     TEXT,
            date   TEXT,
            product TEXT,
            ttype  TEXT,     -- Alış / Satış
            unit   TEXT,     -- adet / gram
            qty    REAL,
            qty_or_gram REAL,
            has_grams REAL,
            note   TEXT
        );
        """))

def read_sql_prices(where: str | None = None, params: dict | None = None) -> pd.DataFrame:
    q = "SELECT source,name,buy,sell,has,ts FROM prices"
    if where: q += " WHERE " + where
    q += " ORDER BY ts DESC"
    with engine.connect() as conn:
        return pd.read_sql(text(q), conn, params=params or {})

def write_prices(df: pd.DataFrame, replace_source: str):
    if df.empty: return
    for c in PRICE_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[PRICE_COLS].copy()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM prices WHERE source=:s"), {"s": replace_source})
        df.to_sql("prices", conn.connection, if_exists="append", index=False)

def append_tx(df: pd.DataFrame):
    if df.empty: return
    with engine.begin() as conn:
        df.to_sql("transactions", conn.connection, if_exists="append", index=False)

def read_tx() -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM transactions ORDER BY ts DESC"), conn)

# ---------- Sayı/parsing yardımcıları ----------
def _normalize_number(x: str) -> float | None:
    if x is None: return None
    s = str(x).strip()
    if not s: return None
    s = s.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def _pairwise_tokens_to_numbers(tokens: list[str]) -> list[str]:
    """
    '5836,65,5924,87' -> ['5836,65','5924,87']
    '9516,9644'       -> ['9516','9644']  (zaten iki parça ise dokunma)
    """
    toks = [t for t in tokens if t != ""]
    if len(toks) == 2:
        return toks
    # çiftli birleştirme
    rebuilt = []
    i = 0
    while i < len(toks):
        if i + 1 < len(toks):
            rebuilt.append(toks[i] + "," + toks[i+1])
            i += 2
        else:
            rebuilt.append(toks[i])
            i += 1
    return rebuilt

def parse_harem_csv(text_block: str) -> pd.DataFrame:
    """
    Harem CSV: Ad,Alış,Satış
    Virgüllü ondalıkları da destekler: 'Gram Altın,5836,65,5924,87'
    """
    rows = []
    for raw in text_block.strip().splitlines():
        if not raw.strip(): continue
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 2: continue
        name = parts[0]
        nums = _pairwise_tokens_to_numbers(parts[1:])
        if len(nums) < 2: continue
        buy = _normalize_number(nums[0])
        sell = _normalize_number(nums[1])
        rows.append({"name": name, "buy": buy, "sell": sell})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["source"] = "HAREM"
        df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
        df["has"] = None
    return df

def parse_ozbag_csv(text_block: str) -> pd.DataFrame:
    """Özbağ CSV: Ad,Has"""
    rows = []
    for raw in text_block.strip().splitlines():
        if not raw.strip(): continue
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 2: continue
        name = parts[0]
        has_val = _normalize_number(",".join(parts[1:]))  # '0,3520' vs '0,3520,0' gibi durumlar
        rows.append({"name": name, "has": has_val})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["source"] = "OZBAG"
        df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
        df["buy"] = None
        df["sell"] = None
    return df

def get_harem_sell_by_any(names: list[str]) -> float | None:
    if not names: return None
    with engine.connect() as conn:
        for n in names:
            res = conn.execute(text("""
                SELECT sell FROM prices
                WHERE source='HAREM' AND name=:n
                ORDER BY ts DESC LIMIT 1
            """), {"n": n}).fetchone()
            if res and res[0] is not None:
                return float(res[0])
    return None

def get_ozbag_has_by_any(names: list[str]) -> float | None:
    if not names: return None
    with engine.connect() as conn:
        for n in names:
            res = conn.execute(text("""
                SELECT has FROM prices
                WHERE source='OZBAG' AND name=:n
                ORDER BY ts DESC LIMIT 1
            """), {"n": n}).fetchone()
            if res and res[0] is not None:
                return float(res[0])
    return None

# ---------- Başlat ----------
ensure_tables()

st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

with st.sidebar:
    st.header("Marj Ayarları")
    st.caption("Öneriler Harem satış fiyatı baz alınarak hesaplanır.")
    gram_buy_delta = st.number_input("24 Ayar Gram Alış (Satış − … TL)", value=20.0, step=1.0)
    gram_sell_delta = st.number_input("24 Ayar Gram Satış (Satış + … TL)", value=10.0, step=1.0)
    st.markdown("---")
    coin_buy_delta = st.number_input("Eski Çeyrek/Yarım/Tam/Ata Alış (Baz − … TL)", value=100.0, step=10.0)
    coin_sell_delta = st.number_input("Eski Çeyrek/Yarım/Tam/Ata Satış (Baz + … TL)", value=50.0, step=10.0)

tab_harem, tab_tx, tab_ozbag, tab_suggest = st.tabs([
    "Harem Fiyatları (Müşteri Bazı)",
    "İşlem (Alış/Satış)",
    "Özbağ Fiyatları (Has Referansı)",
    "Önerilen Fiyatlar",
])

# ---- HAREM ----
with tab_harem:
    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: **Ad,Alış,Satış**  | Örn: `Eski Çeyrek,9516,9644` veya `Gram Altın,5836,65,5924,87`")
    h_txt = st.text_area("CSV'yi buraya yapıştırın", height=120, key="harem_csv")
    if st.button("Harem İçeri Al", type="primary"):
        try:
            df = parse_harem_csv(h_txt)
            if df.empty:
                st.error("Geçerli satır bulunamadı.")
            else:
                write_prices(df, "HAREM")
                st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")
    st.markdown("#### Son Harem Kayıtları")
    st.dataframe(read_sql_prices("source='HAREM'"), use_container_width=True)

# ---- İŞLEM (ALIŞ/SATIŞ) ----
with tab_tx:
    st.subheader("İşlem (Alış/Satış)")
    colL, colR = st.columns([1,1])
    with colL:
        product = st.selectbox("Ürün", PRODUCT_ORDER)
        ttype = st.radio("Tür", ["Satış", "Alış"], horizontal=True)
        unit = "gram" if product == "24 Ayar Gram" else "adet"
        qty = st.number_input("Adet" if unit=="adet" else "Gram", min_value=0.0, value=1.0, step=1.0)

        # has çarpanı (Özbağ'dan)
        has_per_unit = get_ozbag_has_by_any(OZBAG_ALIASES.get(product, [product])) or 0.0
        has_grams = round(has_per_unit * qty, 4) if unit=="adet" else round(1.0 * qty, 4)

        note = st.text_input("Not", "")
        if st.button("Kaydet"):
            row = pd.DataFrame([{
                "ts": dt.datetime.utcnow().isoformat(timespec="seconds"),
                "date": dt.date.today().isoformat(),
                "product": product,
                "ttype": ttype,
                "unit": unit,
                "qty": qty,
                "qty_or_gram": qty,
                "has_grams": has_grams,
                "note": note
            }])
            append_tx(row)
            st.success(f"{product} için {ttype} kaydedildi. (Has: {has_grams} gr)")
    with colR:
        st.caption("Has çarpanı (Özbağ son kayıt): "
                   f"{has_per_unit if product!='24 Ayar Gram' else 1.0}  "
                   f"| Birim: {unit} → Has(gr): {has_grams}")

    st.markdown("#### Son İşlemler")
    tx = read_tx()
    st.dataframe(tx, use_container_width=True)

    total_has = tx["has_grams"].sum() if not tx.empty else 0.0
    st.metric("Toplam Has (gr)", f"{total_has:,.2f}")

# ---- OZBAG ----
with tab_ozbag:
    st.subheader("Özbağ Fiyatları (Toptancı / Has Referansı)")
    st.caption("CSV biçimi: **Ad,Has**  | Örn: `Çeyrek,0,3520`  `Yarım,0,7040`  `Tam,1,4080`  `Ata,1,4160`  `24 Ayar Gram,1,0000`")
    o_txt = st.text_area("CSV'yi buraya yapıştırın", height=120, key="ozbag_csv")
    if st.button("Özbağ İçeri Al"):
        try:
            df = parse_ozbag_csv(o_txt)
            if df.empty:
                st.error("Geçerli satır bulunamadı.")
            else:
                write_prices(df, "OZBAG")
                st.success("Özbağ fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")
    st.markdown("#### Son Özbağ Kayıtları")
    st.dataframe(read_sql_prices("source='OZBAG'"), use_container_width=True)

# ---- ÖNERİLEN ----
with tab_suggest:
    st.subheader("Önerilen Fiyatlar (Marj kurallarıyla)")
    rows = []
    for prod in PRODUCT_ORDER:
        base_sell = get_harem_sell_by_any(HAREM_ALIASES.get(prod, [prod]))
        if base_sell is None:
            rows.append({"ürün": prod, "harem_satış": None, "önerilen_alış": None, "önerilen_satış": None})
            continue
        if prod == "24 Ayar Gram":
            rec_buy  = round(base_sell - gram_buy_delta, 2)
            rec_sell = round(base_sell + gram_sell_delta, 2)
        else:
            rec_buy  = round(base_sell - coin_buy_delta, 2)
            rec_sell = round(base_sell + coin_sell_delta, 2)
        rows.append({"ürün": prod, "harem_satış": base_sell,
                     "önerilen_alış": rec_buy, "önerilen_satış": rec_sell})
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
    st.caption("Not: ‘Eski Çeyrek/Yarım/Tam/Ata’ ve ‘Gram Altın’ satırları baz alınır. Gram için Harem **satış** ± marj kullanılır.")