# app.py — Sarıkaya Kuyumculuk (sıfırdan)
# Streamlit 1.38+  / Python 3.12+
# - Harem & Özbağ CSV içeri al
# - Marj kuralı: Gram altın = Harem Satış -20 (alış) / +10 (satış)
# - Eski Çeyrek / Yarım / Tam / Ata = Harem “Eski …” satırına göre
# - İşlem (alış/satış) paneli: canlı öneri (10 sn’de bir yeniden hesap)
# - Envanter (has bazlı özet) + Kasa ekranı
# - Özbağ tedarikçisi için borç/has takibi (+ 22 ayar bilezik girişleri)

from __future__ import annotations
import io
import sqlite3
import datetime as dt
from typing import Optional, List, Dict

import pandas as pd
import streamlit as st

DB_PATH = "sar_kaya.db"

# ---------- yardımcılar ----------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS prices(
        source TEXT,
        name   TEXT,
        buy    REAL,
        sell   REAL,
        ts     TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        date TEXT,
        product TEXT,
        ttype TEXT,         -- 'Alış' | 'Satış'
        unit  TEXT,         -- 'adet' | 'gram'
        qty_or_gram REAL,   -- adet->adet, gram->gram
        unit_price REAL,    -- TL birim
        total_tl   REAL,
        note TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ozbag_ledger(
        date TEXT,
        item TEXT,          -- 'Bilezik 22A' vb.
        has_grams REAL,     -- (+) borç artar, (-) borç düşer
        tl REAL,            -- bilgi amaçlı
        note TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        val TEXT
    )
    """)
    conn.commit()
    return conn

def write_df(table: str, df: pd.DataFrame):
    conn = get_conn()
    df.to_sql(table, conn, if_exists="append", index=False)
    conn.close()

def read_sql(q: str, params: dict | tuple | None = None) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df

def upsert_setting(key: str, val: str):
    conn = get_conn()
    conn.execute("INSERT INTO settings(key,val) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET val=excluded.val", (key, val))
    conn.commit()
    conn.close()

def get_setting(key: str, default: str) -> str:
    conn = get_conn()
    cur = conn.execute("SELECT val FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

# ---------- ürün/özellikler ----------
PRODUCTS: Dict[str, Dict] = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75, "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50, "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00, "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00, "purity": 0.995},
    "22 Ayar Gram": {"unit": "gram", "std_weight": 1.00, "purity": 0.916},
    "22 Ayar 0,5g": {"unit": "adet", "std_weight": 0.50, "purity": 0.916},
    "22 Ayar 0,25g": {"unit":"adet","std_weight": 0.25, "purity": 0.916},
}

# Harem eş adları (Eski ... ve Gram Altın)
HAREM_NAME_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım", "Yarım"],
    "Tam Altın"   : ["Eski Tam", "Tam"],
    "Ata Lira"    : ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram"],
}

# ---------- CSV içe alma ----------
def parse_simple_csv(txt: str, cols: List[str]) -> pd.DataFrame:
    # sadelik: her satır "Ad, v1, v2" şeklinde
    df = pd.read_csv(io.StringIO(txt), header=None)
    if len(df.columns) != len(cols):
        raise ValueError(f"Beklenen {len(cols)} sütun: {cols}")
    df.columns = cols
    return df

def import_harem_csv(txt: str):
    """
    Beklenen CSV (örnek):
    Eski Çeyrek,9516,9644
    Eski Yarım,19100,19300
    Eski Tam,38200,38600
    Eski Ata,38400,38800
    Gram Altın,5836.65,5924.87
    """
    df = parse_simple_csv(txt, ["name", "buy", "sell"])
    df["source"] = "HAREM"
    df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    # float düzeltme: virgüllü gelebilir
    for c in ["buy", "sell"]:
        df[c] = df[c].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        df[c] = pd.to_numeric(df[c], errors="coerce")
    write_df("prices", df[["source","name","buy","sell","ts"]])

def import_ozbag_csv(txt: str):
    """
    Beklenen CSV (örnek, has çarpanı):
    Çeyrek,0.3520
    Yarım,0.7040
    Tam,1.4080
    Ata,1.4160
    24 Ayar Gram,1.0000
    """
    df = parse_simple_csv(txt, ["name", "has"])
    df["has"] = pd.to_numeric(df["has"], errors="coerce")
    df["source"] = "OZBAG"
    df["buy"] = None
    df["sell"] = df["has"]   # referans olarak sell sütununa yazıyoruz (kolay okunsun)
    df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    write_df("prices", df[["source","name","buy","sell","ts"]])

# ---------- fiyat okuma & öneri ----------
def get_price_by_any(src: str, candidates: List[str], which: str) -> Optional[float]:
    # en güncel kaydı al
    q = """
    SELECT name, buy, sell, ts FROM prices
    WHERE source = :src
      AND name IN ({})
    ORDER BY ts DESC
    LIMIT 50
    """.format(",".join(["?"]*len(candidates)))
    params = (src, *candidates)
    df = read_sql(q, params)
    if df.empty: 
        return None
    # listedeki ilk eşleşeni bul
    for cand in candidates:
        m = df[df["name"] == cand]
        if not m.empty:
            return float(m.iloc[0][which])
    # yoksa en üsttekini ver
    return float(df.iloc[0][which])

def suggested_price(product_name: str, ttype: str) -> Optional[float]:
    # kaynaklar: HAREM (temel), Özbağ has çarpanı sadece bilgi
    aliases = HAREM_NAME_ALIASES.get(product_name, [product_name])
    base_sell = get_price_by_any("HAREM", aliases, "sell")  # Harem satış kolonunu baz alıyoruz
    if base_sell is None:
        return None

    # Gram altın özel marj: -20 / +10
    if product_name == "24 Ayar Gram":
        return base_sell - 20 if ttype == "Alış" else base_sell + 10

    # Sikke/ata için basit marj kuralı (istersen ayarlardan değiştirirsin)
    coin_buy_margin  = float(get_setting("coin_buy_sub",  "50"))   # Harem satıştan şu kadar aşağı al
    coin_sell_margin = float(get_setting("coin_sell_add", "50"))   # Harem satıştan şu kadar yukarı sat
    if ttype == "Alış":
        return base_sell - coin_buy_margin
    else:
        return base_sell + coin_sell_margin

# ---------- envanter & kasa ----------
def compute_inventory() -> pd.DataFrame:
    tx = read_sql("SELECT * FROM transactions")
    if tx.empty:
        return pd.DataFrame(columns=["product","unit","qty","has_grams"])
    def row_has(r):
        meta = PRODUCTS.get(r["product"], {"std_weight":0,"purity":1,"unit":"adet"})
        grams = r["qty_or_gram"] if meta["unit"]=="gram" else r["qty_or_gram"]*meta["std_weight"]
        grams *= meta["purity"]
        return grams if r["ttype"]=="Alış" else -grams
    def row_qty(r):
        return r["qty_or_gram"] if PRODUCTS.get(r["product"],{}).get("unit")=="gram" else (r["qty_or_gram"] if r["ttype"]=="Alış" else -r["qty_or_gram"])
    tx["q_delta"]  = tx.apply(row_qty, axis=1)
    tx["h_delta"]  = tx.apply(row_has, axis=1)
    inv = tx.groupby("product", as_index=False).agg(qty=("q_delta","sum"), has_grams=("h_delta","sum"))
    inv["unit"] = inv["product"].map(lambda p: PRODUCTS[p]["unit"] if p in PRODUCTS else "")
    return inv

def compute_cash() -> float:
    tx = read_sql("SELECT * FROM transactions")
    if tx.empty:
        return 0.0
    # satış -> +, alış -> -
    tx["delta"] = tx.apply(lambda r: r["total_tl"] if r["ttype"]=="Satış" else -r["total_tl"], axis=1)
    return float(tx["delta"].sum())

def ozbag_balance_has() -> float:
    df = read_sql("SELECT * FROM ozbag_ledger")
    if df.empty:
        return 0.0
    return float(df["has_grams"].sum())

# ---------- UI ----------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", page_icon="💎", layout="wide")

tabs = st.tabs(["Harem Fiyatları (Müşteri Bazı)", "İşlem (Alış/Satış)", "Özbağ Fiyatları (Has Referansı)", "Kasa / Envanter"])

# ============== TAB 1: HAREM ==============
with tabs[0]:
    st.header("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek satırlar aşağıda.")
    default_harem = """Eski Çeyrek,9516,9644
Eski Yarım,19100,19300
Eski Tam,38200,38600
Eski Ata,38400,38800
Gram Altın,5836.65,5924.87"""
    h_txt = st.text_area("CSV'yi buraya yapıştırın", value=default_harem, height=140, key="harem_csv")
    colh1, colh2 = st.columns([1,1])
    if colh1.button("Harem İçeri Al", use_container_width=True):
        try:
            import_harem_csv(h_txt.strip())
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    # son kayıtlar
    last_harem = read_sql("SELECT * FROM prices WHERE source='HAREM' ORDER BY ts DESC LIMIT 200")
    st.data_editor(last_harem, use_container_width=True, height=320, disabled=True)

# ============== TAB 2: İŞLEM ==============
with tabs[1]:
    st.header("İşlem (Alış/Satış)")
    st.caption("Öneri fiyatı Harem’deki son kayda göre **10 sn** aralıkla otomatik güncellenir.")

    # auto-refresh
    st_autorefresh = st.experimental_rerun  # sadece isim kısaltma
    st.experimental_set_query_params(_=dt.datetime.utcnow().timestamp())  # URL cache kırılsın
    st.empty()  # focus bug fix

    product = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype   = st.radio("Tür", ["Satış","Alış"], horizontal=True, index=1 if "Alış" else 0)
    unit    = PRODUCTS[product]["unit"]

    if unit == "adet":
        qty = st.number_input("Adet", min_value=1.0, value=1.0, step=1.0)
        qty_label = "Adet"
    else:
        qty = st.number_input("Gram", min_value=0.01, value=1.00, step=0.10)
        qty_label = "Gram"

    # canlı öneri
    suggested = suggested_price(product, ttype)
    st.info(f"Önerilen {ttype} fiyatı: **{suggested:.2f} ₺**" if suggested else "Harem verisi eksik.", icon="💡")

    price_input = st.number_input("Birim Fiyat (₺)", min_value=0.0, value=float(suggested or 0.0), step=1.0, format="%.2f")
    note = st.text_input("Not")

    # güvenlik: öneri altı satış / üstü alış uyarısı
    warn = ""
    if suggested is not None:
        if ttype=="Satış" and price_input < suggested:
            warn = "⚠️ Satış fiyatı önerinin altında!"
        if ttype=="Alış"  and price_input > suggested:
            warn = "⚠️ Alış fiyatı önerinin üstünde!"

    if warn:
        st.warning(warn)

    total = qty * price_input
    st.metric(label="Toplam (TL)", value=f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X","."))

    if st.button("Kaydet", type="primary"):
        rec = pd.DataFrame([{
            "date": dt.datetime.utcnow().isoformat(timespec="seconds"),
            "product": product,
            "ttype": ttype,
            "unit": unit,
            "qty_or_gram": float(qty),
            "unit_price": float(price_input),
            "total_tl": float(total),
            "note": note
        }])
        write_df("transactions", rec)
        st.success(f"{product} için {ttype} kaydedildi.")

    st.subheader("Son İşlemler")
    tx = read_sql("SELECT * FROM transactions ORDER BY date DESC LIMIT 200")
    st.data_editor(tx, use_container_width=True, height=320, disabled=True)

# ============== TAB 3: ÖZBAĞ ==============
with tabs[2]:
    st.header("Özbağ Fiyatları (Has Referansı)")
    st.caption("CSV biçimi: Ad,Has  | Örnek: Çeyrek,0.3520  (24 Ayar Gram için 1.0)")
    default_oz = """Çeyrek,0.3520
Yarım,0.7040
Tam,1.4080
Ata,1.4160
24 Ayar Gram,1.0000"""
    o_txt = st.text_area("CSV'yi buraya yapıştırın", value=default_oz, height=140, key="ozbag_csv")
    if st.button("Özbağ İçeri Al", use_container_width=True):
        try:
            import_ozbag_csv(o_txt.strip())
            st.success("Özbağ kaydı alındı.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.divider()
    st.subheader("Özbağ Borç (Has) İşlemleri")
    col1, col2, col3 = st.columns(3)
    oz_item = col1.selectbox("Kalem", ["Bilezik 22A", "Sikke", "Has Düşüş (Ödeme)", "Diğer"])
    oz_has  = col2.number_input("Has (gr)", value=0.0, step=0.10, help="+ borç artar, - borç azalır")
    oz_tl   = col3.number_input("TL (opsiyonel)", value=0.0, step=10.0)
    oz_note = st.text_input("Not (örn. işçilik, adet vb.)")
    if st.button("Özbağ Kaydet"):
        rec = pd.DataFrame([{
            "date": dt.datetime.utcnow().isoformat(timespec="seconds"),
            "item": oz_item,
            "has_grams": float(oz_has),
            "tl": float(oz_tl),
            "note": oz_note
        }])
        write_df("ozbag_ledger", rec)
        st.success("Özbağ hareketi kaydedildi.")

    st.metric("Özbağ’a Has Borç (gr)", f"{ozbag_balance_has():,.2f}".replace(",", "X").replace(".", ",").replace("X","."))
    last_oz = read_sql("SELECT * FROM prices WHERE source='OZBAG' ORDER BY ts DESC LIMIT 200")
    st.data_editor(last_oz, use_container_width=True, height=240, disabled=True)
    st.subheader("Özbağ Defteri")
    oz_ledger = read_sql("SELECT * FROM ozbag_ledger ORDER BY date DESC")
    st.data_editor(oz_ledger, use_container_width=True, height=300, disabled=True)

# ============== TAB 4: KASA / ENVANTER ==============
with tabs[3]:
    st.header("Kasa / Envanter")
    inv = compute_inventory()
    cash = compute_cash()

    # istenen kalemleri sırayla göster
    wanted = ["Çeyrek Altın","Yarım Altın","Tam Altın","Ata Lira",
              "24 Ayar Gram","22 Ayar Gram","22 Ayar 0,5g","22 Ayar 0,25g"]

    rows = []
    for name in wanted:
        row = inv[inv["product"]==name]
        if row.empty:
            rows.append({"ürün":name, "miktar":0.0, "birim":PRODUCTS[name]["unit"], "has(gr)":0.0})
        else:
            r = row.iloc[0]
            rows.append({"ürün":name, "miktar":round(r["qty"],3), "birim":PRODUCTS[name]["unit"], "has(gr)":round(r["has_grams"],3)})
    kasadf = pd.DataFrame(rows)
    st.data_editor(kasadf, use_container_width=True, disabled=True)
    st.metric("TL Kasa", f"{cash:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X","."))

    st.subheader("Envanter (Has Bazlı Özet)")
    st.data_editor(inv, use_container_width=True, disabled=True)