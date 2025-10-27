import re, io, time, math, datetime as dt
from typing import Optional, List, Tuple
import pandas as pd
from sqlalchemy import create_engine, text
import streamlit as st

# ---------- Kalıcı DB ----------
ENGINE = create_engine("sqlite:///data.db", future=True)

def ensure_tables():
    with ENGINE.begin() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS prices(
            id INTEGER PRIMARY KEY,
            source TEXT,      -- HAREM
            name   TEXT,      -- Eski Çeyrek / Gram Altın ...
            buy    REAL,
            sell   REAL,
            ts     TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY,
            date TEXT,
            product TEXT,
            ttype TEXT,          -- Alış / Satış
            unit TEXT,           -- adet / gram
            qty REAL,            -- kullanıcı girişi
            qty_grams REAL,      -- has/gram bazına çevrilmiş
            unit_price REAL,     -- TL birim
            note TEXT
        )""")
ensure_tables()

# ---------- Ürün tanımları & eşadlar ----------
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995},
}

# Harem tarafındaki isimler için esnek eş-adlar
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım", "Yarım"],
    "Tam Altın"   : ["Eski Tam", "Tam"],
    "Ata Lira"    : ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar"],
}

# ---------- Yardımcılar ----------
def parse_tr_number(s: str) -> float:
    """
    '5.924,87' → 5924.87
    '5,924.87' → 5924.87
    '5924,87'  → 5924.87
    '5924.87'  → 5924.87
    '9,516'    → 9516
    """
    s = s.strip()
    # doğrusal kural: sonunda ,dd varsa virgül ondalık kabul
    if re.search(r",\d{1,2}$", s):
        s = s.replace(".", "").replace(",", ".")
        return float(s)
    # yoksa noktayı ondalık varsay, virgülü sil
    return float(s.replace(",", ""))

def read_prices() -> pd.DataFrame:
    with ENGINE.begin() as c:
        df = pd.read_sql("SELECT * FROM prices ORDER BY ts DESC, id DESC", c)
    return df

def write_prices(df: pd.DataFrame):
    df = df.copy()
    with ENGINE.begin() as c:
        df.to_sql("prices", c, if_exists="append", index=False)

def read_tx() -> pd.DataFrame:
    with ENGINE.begin() as c:
        df = pd.read_sql("SELECT * FROM transactions ORDER BY date DESC, id DESC", c)
    return df

def write_tx(row: dict):
    with ENGINE.begin() as c:
        c.execute(text("""
            INSERT INTO transactions(date,product,ttype,unit,qty,qty_grams,unit_price,note)
            VALUES (:date,:product,:ttype,:unit,:qty,:qty_grams,:unit_price,:note)
        """), row)

def latest_price_by_any(source: str, names: List[str], field: str) -> Optional[float]:
    df = read_prices()
    df = df[df["source"] == source]
    for n in names:
        m = df[df["name"].str.lower() == n.lower()]
        if not m.empty and field in ("buy", "sell"):
            try:
                return float(m.iloc[0][field])
            except Exception:
                continue
    return None

def suggested_price(product: str, ttype: str) -> Optional[float]:
    """
    24 Ayar Gram: Harem son SATIŞ -> alış = -20, satış = +10
    Sikkeler: Eski Çeyrek/Yarım/Tam/Ata'nın Harem SATIŞ'ına göre (aynen).
    """
    alias = HAREM_ALIASES.get(product, [product])

    if product == "24 Ayar Gram":
        base_sell = latest_price_by_any("HAREM", alias, "sell")
        if base_sell is None:
            return None
        if ttype == "Alış":
            return base_sell - 20.0
        else:
            return base_sell + 10.0

    # Sikke/ata için: Harem satış baz alınır
    base = latest_price_by_any("HAREM", alias, "sell")
    return base

def to_has_grams(product: str, qty: float) -> float:
    meta = PRODUCTS[product]
    if meta["unit"] == "adet":
        return qty * meta["std_weight"] * meta["purity"]
    return qty * meta["purity"]

# ---------- UI ----------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="centered")

st.title("💎 Sarıkaya Kuyumculuk\n– Entegrasyon")

tabs = st.tabs(["📊 Harem Fiyatları", "💱 Alış / Satış", "🏦 Kasa & Envanter"])

# ====== TAB 1: HAREM FİYATLARI ======
with tabs[0]:
    st.subheader("Harem Fiyatları (CSV/Yapıştır)")
    st.caption("Biçim: **Ad,Alış,Satış**  | Örnekler aşağıda. Türkçe ya da İngilizce sayı biçimleri kabul edilir.")

    sample = st.selectbox(
        "Örnek seç",
        [
            "Boş",
            "TR biçimi",
            "EN biçimi"
        ],
        index=0
    )

    if sample == "TR biçimi":
        example = """Eski Çeyrek,9.516,9.644
Eski Yarım,19.100,19.300
Eski Tam,38.200,38.600
Eski Ata,38.400,38.800
Gram Altın,5.728,68,5.807,08"""
    elif sample == "EN biçimi":
        example = """Eski Çeyrek,9516,9644
Eski Yarım,19100,19300
Eski Tam,38200,38600
Eski Ata,38400,38800
Gram Altın,5728.68,5807.08"""
    else:
        example = ""

    txt = st.text_area("CSV'yi buraya yapıştırın", value=example, height=140, key="harem_csv")
    if st.button("Harem İçeri Al"):
        try:
            rows: List[Tuple[str, float, float]] = []
            for line in [l for l in txt.splitlines() if l.strip()]:
                parts = [p.strip() for p in re.split(r",\s*", line)]
                # Gram Altın satırında TR biçiminde 4 parça olabiliyor (5.728,68)
                if len(parts) == 4 and "gram" in parts[0].lower():
                    name = parts[0]
                    buy  = parse_tr_number(parts[1] + "," + parts[2])
                    sell = parse_tr_number(parts[3])
                elif len(parts) == 3:
                    name = parts[0]
                    buy  = parse_tr_number(parts[1])
                    sell = parse_tr_number(parts[2])
                else:
                    raise ValueError(f"Satır hatalı: {line}")
                rows.append((name, buy, sell))

            df = pd.DataFrame(rows, columns=["name", "buy", "sell"])
            df["source"] = "HAREM"
            df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
            df = df[["source", "name", "buy", "sell", "ts"]]
            write_prices(df)
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("**Son Harem Kayıtları**")
    st.dataframe(read_prices(), use_container_width=True, height=260)

# ====== TAB 2: ALIŞ / SATIŞ ======
with tabs[1]:
    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri, Harem'deki **son satış** satırından hesaplanır. İstersen fiyatı elle değiştirebilirsin.")

    product = st.selectbox("Ürün Seç", list(PRODUCTS.keys()))
    ttype   = st.radio("İşlem Türü", ["Alış", "Satış"], horizontal=True)
    qty     = st.number_input("Adet / Gram", min_value=0.01, step=1.0, value=1.0)

    suggested = suggested_price(product, ttype)
    if suggested is None:
        st.warning("Öneri oluşturulamadı. Lütfen Harem fiyatlarını gir.")
        suggested = 0.0

    unit_price = st.number_input("Birim Fiyat (TL)", step=1.0, value=float(round(suggested, 2)))

    total = unit_price * qty
    st.metric("Toplam", f"{total:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X", "."))

    # Güvenlik uyarıları
    if product == "24 Ayar Gram":
        harem_sell = latest_price_by_any("HAREM", HAREM_ALIASES["24 Ayar Gram"], "sell")
        if harem_sell is not None:
            floor = harem_sell - 20
            ceil  = harem_sell + 10
            if ttype == "Alış" and unit_price > floor + 1e-6:
                st.error(f"Uyarı: Alış fiyatı kuralı aşıyor (<= {floor:.2f} TL olmalı).")
            if ttype == "Satış" and unit_price < ceil - 1e-6:
                st.error(f"Uyarı: Satış fiyatı kuralın altında (>= {ceil:.2f} TL olmalı).")

    note = st.text_input("Not", "")

    if st.button("Kaydet"):
        meta = PRODUCTS[product]
        qty_grams = to_has_grams(product, qty)
        row = {
            "date": dt.datetime.now().isoformat(timespec="seconds"),
            "product": product,
            "ttype": ttype,
            "unit": meta["unit"],
            "qty": qty,
            "qty_grams": qty_grams,
            "unit_price": unit_price,
            "note": note
        }
        write_tx(row)
        st.success("İşlem kaydedildi.")

    st.markdown("**Son İşlemler**")
    st.dataframe(read_tx(), use_container_width=True, height=260)

# ====== TAB 3: KASA & ENVANTER ======
with tabs[2]:
    st.subheader("Kasa & Envanter")
    tx = read_tx()
    if tx.empty:
        st.info("Henüz işlem yok.")
    else:
        # stok (+alış, -satış)
        sign = tx["ttype"].map({"Alış": 1, "Satış": -1}).fillna(0)
        tx["stock_grams"] = tx["qty_grams"] * sign
        inv = tx.groupby("product", as_index=False)["stock_grams"].sum()
        st.markdown("**Has Bazlı Envanter (gr)**")
        st.dataframe(inv, use_container_width=True)

        # TL kasa: Satış +, Alış -
        cash = (tx["unit_price"] * tx["qty"] * sign * -1).sum()
        st.metric("Kasa (TL)", f"{cash:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X", "."))

        st.markdown("**İşlem Listesi**")
        st.dataframe(tx, use_container_width=True, height=280)