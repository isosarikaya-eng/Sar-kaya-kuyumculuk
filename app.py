# app.py
# -*- coding: utf-8 -*-

import io
import datetime as dt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# ============== Genel Ayarlar ==============
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")

DB_URL = "sqlite:///sarikaya_kuyum.db"
engine = create_engine(DB_URL, future=True)

# Tablomuzun standart kolon düzeni
PRICE_COLS = ["source", "name", "buy", "sell", "has", "ts"]

# Harem'deki isim eşleştirmeleri (öncelik sırasıyla)
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın": ["Eski Yarım", "Yarım"],
    "Tam Altın": ["Eski Tam", "Tam"],
    "Ata Lira":   ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram", "Has Altın"],  # “Has Altın” bazı ekranlarda gramı ifade ediyor
}

PRODUCT_ORDER = ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"]


# ============== Yardımcılar ==============
def _normalize_number(x: str) -> float | None:
    """
    Türkçe sayıları normalize eder:
    - Binlik ayırıcı nokta '.' kaldırılır
    - Ondalık ayracı ',' -> '.'
    - Boş/None için None döner
    """
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    # Örn: 5.836,65 -> 5836,65
    s = s.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def ensure_table():
    """prices tablosu yoksa oluşturur."""
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


def read_sql(where: str | None = None, params: dict | None = None) -> pd.DataFrame:
    q = "SELECT source,name,buy,sell,has,ts FROM prices"
    if where:
        q += " WHERE " + where
    q += " ORDER BY ts DESC"
    with engine.connect() as conn:
        df = pd.read_sql(text(q), conn, params=params or {})
    return df


def write_df(df: pd.DataFrame, replace_source: str):
    """
    Aynı 'source' için eskileri siler, df'i ekler.
    df kolonları PRICE_COLS sırasına getirilir.
    """
    if df.empty:
        return
    # Kolonları garantiye al
    for c in PRICE_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[PRICE_COLS].copy()

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM prices WHERE source = :s"), {"s": replace_source})
        df.to_sql("prices", conn.connection, if_exists="append", index=False)


def parse_harem_csv(text_block: str) -> pd.DataFrame:
    """
    Harem CSV: Ad,Alış,Satış
    Virgül/nokta farkı otomatik normalize edilir.
    """
    rows = []
    for line in text_block.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            # Ad,Alış,Satış bekliyoruz
            continue
        name = parts[0]
        buy = _normalize_number(parts[1])
        sell = _normalize_number(parts[2])
        rows.append({"name": name, "buy": buy, "sell": sell})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["source"] = "HAREM"
        df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
        df["has"] = None
    return df


def parse_ozbag_csv(text_block: str) -> pd.DataFrame:
    """
    Özbağ CSV: Ad,Has
    """
    rows = []
    for line in text_block.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        has_val = _normalize_number(parts[1])
        rows.append({"name": name, "has": has_val})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["source"] = "OZBAG"
        df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
        df["buy"] = None
        df["sell"] = None
    return df


def get_harem_sell_by_any(names: list[str]) -> float | None:
    """
    Verilen isimlerden ilk bulunanın HAREM satışını (son kayıt) getir.
    """
    if not names:
        return None
    with engine.connect() as conn:
        for n in names:
            q = text("""
                SELECT sell FROM prices
                WHERE source='HAREM' AND name=:n
                ORDER BY ts DESC LIMIT 1
            """)
            res = conn.execute(q, {"n": n}).fetchone()
            if res and res[0] is not None:
                return float(res[0])
    return None


# ============== Arayüz ==============
ensure_table()

st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

# Kenar panel: marj ayarları
with st.sidebar:
    st.header("Marj Ayarları")
    st.caption("Öneri hesabında Harem satış fiyatı baz alınır.")
    gram_buy_delta = st.number_input("24 Ayar Gram Alış (Satış − … TL)", value=20.0, step=1.0)
    gram_sell_delta = st.number_input("24 Ayar Gram Satış (Satış + … TL)", value=10.0, step=1.0)

    st.markdown("---")
    coin_buy_delta = st.number_input("Eski Çeyrek/Yarım/Tam/Ata Alış (Baz − … TL)", value=100.0, step=10.0)
    coin_sell_delta = st.number_input("Eski Çeyrek/Yarım/Tam/Ata Satış (Baz + … TL)", value=50.0, step=10.0)

tab1, tab2, tab3 = st.tabs(["Harem Fiyatları (Müşteri Bazı)", "Özbağ Fiyatları (Has Referansı)", "Önerilen Fiyatlar"])


# -------- HAREM ----------
with tab1:
    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: **Ad,Alış,Satış**  | Örnek: `Eski Çeyrek,9516,9644`  veya `Gram Altın,5.836,65,5.924,87`")

    h_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv")
    if st.button("Harem İçeri Al", type="primary"):
        try:
            df = parse_harem_csv(h_txt)
            if df.empty:
                st.error("Geçerli satır bulunamadı. Lütfen `Ad,Alış,Satış` biçimini kullanın.")
            else:
                write_df(df, "HAREM")
                st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Harem Fiyatları")
    h_last = read_sql("source='HAREM'")
    st.dataframe(h_last, use_container_width=True)


# -------- OZBAG ----------
with tab2:
    st.subheader("Özbağ Fiyatları (Toptancı / Has Referansı)")
    st.caption("CSV biçimi: **Ad,Has**  | Örnek: `Çeyrek,0,3520`  `Yarım,0,7040`  `Tam,1,4080`  `Ata,1,4160`  `24 Ayar Gram,1,0000`")

    o_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="ozbag_csv")
    if st.button("Özbağ İçeri Al"):
        try:
            df = parse_ozbag_csv(o_txt)
            if df.empty:
                st.error("Geçerli satır bulunamadı. Lütfen `Ad,Has` biçimini kullanın.")
            else:
                write_df(df, "OZBAG")
                st.success("Özbağ fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Özbağ Fiyatları")
    o_last = read_sql("source='OZBAG'")
    st.dataframe(o_last, use_container_width=True)


# -------- ÖNERİLEN ----------
with tab3:
    st.subheader("Önerilen Fiyatlar (Marj kurallarıyla)")

    rows = []
    for prod in PRODUCT_ORDER:
        aliases = HAREM_ALIASES.get(prod, [prod])
        base_sell = get_harem_sell_by_any(aliases)

        if base_sell is None:
            rows.append({"ürün": prod, "harem_satış": None, "önerilen_alış": None, "önerilen_satış": None})
            continue

        if prod == "24 Ayar Gram":
            rec_buy = round(base_sell - gram_buy_delta, 2)
            rec_sell = round(base_sell + gram_sell_delta, 2)
        else:
            rec_buy = round(base_sell - coin_buy_delta, 2)
            rec_sell = round(base_sell + coin_sell_delta, 2)

        rows.append({
            "ürün": prod,
            "harem_satış": base_sell,
            "önerilen_alış": rec_buy,
            "önerilen_satış": rec_sell
        })

    rec_df = pd.DataFrame(rows)
    st.dataframe(rec_df, use_container_width=True)

    st.caption("Not: Öneri hesabında Harem’de **Eski Çeyrek/Yarım/Tam/Ata** ve **Gram Altın** satırları baz alınır. "
               "Marjlar sol panelden değiştirilebilir.")