# -- coding: utf-8 --

"""
Sarıkaya Kuyumculuk – Has Bazlı Envanter & Fiyat Entegrasyonu
Streamlit Cloud veya yerel ortamda çalışır.
Gereken paketler: streamlit, pandas
"""

import io
import sqlite3
import datetime as dt
import pandas as pd
import streamlit as st

DB_PATH = "sarıkaya_kuyum.db"

# ============== Yardımcılar: DB ==================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices(
            source TEXT,  -- HAREM / OZBAG
            name   TEXT,
            buy    REAL,
            sell   REAL,
            ts     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
            date            TEXT,
            product         TEXT,  -- Çeyrek Altın vb
            ttype           TEXT,  -- Alış / Satış
            unit            TEXT,  -- adet / gram
            qty_or_gram     REAL,
            unit_price_used REAL,
            amount          REAL,
            has_grams       REAL,
            note            TEXT,
            created_at      TEXT
        )
    """)
    return conn

def write_df(table: str, df: pd.DataFrame):
    if df is None or df.empty:
        return
    conn = db()
    df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()

def read_sql(q: str, params: tuple = ()):
    conn = db()
    return pd.read_sql_query(q, conn, params=params)

def clear_source_in_prices(source: str):
    conn = db()
    conn.execute("DELETE FROM prices WHERE source=?", (source,))
    conn.commit()

def latest_prices(source: str) -> pd.DataFrame:
    """Kaydedilmiş son HAREM/OZBAG fiyat listesi (her adımdaki en son kayıtları döndürür)."""
    df = read_sql("SELECT * FROM prices WHERE source=? ORDER BY ts DESC", (source,))
    if df.empty:
        return df
    # aynı isimden birden çok kayıt varsa en son ts'li olanı al
    df = df.drop_duplicates(subset=["name"], keep="first")
    return df[["name", "buy", "sell", "ts"]].reset_index(drop=True)

# ============== Ürünler & Marjlar =================
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916, "sell_add": 50.0,  "buy_sub": 50.0},
    "Yarım Altın":  {"unit": "adet", "std_weight": 3.50,  "purity": 0.916, "sell_add": 100.0, "buy_sub": 100.0},
    "Tam Altın":    {"unit": "adet", "std_weight": 7.00,  "purity": 0.916, "sell_add": 200.0, "buy_sub": 200.0},
    "Ata Lira":     {"unit": "adet", "std_weight": 7.216, "purity": 0.916, "sell_add": 200.0, "buy_sub": 200.0},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995, "sell_add": 10.0,  "buy_sub": 20.0},
}

# Harem tarafındaki isimler için esnek eş-adlar
HAREM_NAME_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım", "Yarım"],
    "Tam Altın":    ["Eski Tam", "Tam"],
    "Ata Lira":     ["Eski Ata", "Ata", "Ata Lira"],
    "24 Ayar Gram": ["Gram 24 Ayar", "24 Ayar Gram"],
}

def get_price_by_any(source: str, names: list[str], field: str = "sell") -> float | None:
    """Verilen isim adaylarından ilk bulunanın fiyatını getirir (HAREM/OZBAG)."""
    df = latest_prices(source)
    if df.empty:
        return None
    for nm in names:
        m = df[df["name"] == nm]
        if not m.empty:
            return float(m.iloc[0][field])
    return None


def suggested_price(product_name: str, ttype: str) -> float | None:
    """Önerilen kasa fiyatı: HAREM baz sell +/− marj."""
    aliases = HAREM_NAME_ALIASES.get(product_name, [product_name])
    base = get_price_by_any("HAREM", aliases, "sell")
    if base is None:
        return None
