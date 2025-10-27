# app.py
# Sarıkaya Kuyumculuk – Harem bazlı fiyat entegrasyonu
# - Harem CSV yapıştır: name,buy,sell  (ör: "Gram Altın,5728.68,5807.08")
# - Alış/Satış: Canlı (10 sn) öneri, manuel fiyat girme, eşik uyarıları
# - Özbağ entegrasyonu yok (istersen sonradan ekleriz)

from __future__ import annotations
import io, re, datetime as dt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# ---------- Kalıcı veritabanı (SQLite) ----------
ENGINE = create_engine("sqlite:///sar_kaya.db", future=True)

def init_db():
    with ENGINE.begin() as con:
        con.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS prices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            name   TEXT NOT NULL,
            buy    REAL NOT NULL,
            sell   REAL NOT NULL,
            ts     TEXT NOT NULL
        );
        """)
        con.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date   TEXT NOT NULL,
            product TEXT NOT NULL,
            ttype   TEXT NOT NULL,    -- "Alış" / "Satış"
            unit    TEXT NOT NULL,    -- "adet" / "gram"
            qty     REAL NOT NULL,
            unit_price REAL NOT NULL, -- manuel girilen veya öneri
            total   REAL NOT NULL,
            note    TEXT
        );
        """)
init_db()

# ---------- Yardımcılar ----------
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995},
    "22 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.916},
    "22 Ayar 0,5g": {"unit": "adet", "std_weight": 0.50,  "purity": 0.916},
    "22 Ayar 0,25g": {"unit": "adet", "std_weight": 0.25, "purity": 0.916},
}

# Harem isim eşleştirme – öncelik Eski … serisine
HAREM_ALIAS = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım" , "Yarım"],
    "Tam Altın"   : ["Eski Tam"   , "Tam"],
    "Ata Lira"    : ["Eski Ata"   , "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar"],
    "22 Ayar Gram": ["22 Ayar Gram", "22 Ayar"],
    "22 Ayar 0,5g": ["22 Ayar 0,5", "0,5g 22 Ayar"],
    "22 Ayar 0,25g": ["22 Ayar 0,25", "0,25g 22 Ayar"],
}

# Varsayılan marjlar (istediğin gibi güncelleyebilirsin)
DEFAULT_MARGINS = {
    # öneri: Harem SELL fiyatından hesaplanır
    # buy_offset: öneri alış = harem_sell + buy_offset
    # sell_offset: öneri satış = harem_sell + sell_offset
    "Çeyrek Altın": {"buy_offset": -50.0, "sell_offset": +50.0},
    "Yarım Altın" : {"buy_offset": -100.0, "sell_offset": +100.0},
    "Tam Altın"   : {"buy_offset": -200.0, "sell_offset": +200.0},
    "Ata Lira"    : {"buy_offset": -200.0, "sell_offset": +200.0},
    "24 Ayar Gram": {"buy_offset": -20.0,  "sell_offset": +10.0},  # senin kuralın
    "22 Ayar Gram": {"buy_offset": -20.0,  "sell_offset": +10.0},
    "22 Ayar 0,5g": {"buy_offset": -10.0,  "sell_offset": +15.0},
    "22 Ayar 0,25g":{"buy_offset": -5.0,   "sell_offset": +10.0},
}

if "MARGINS" not in st.session_state:
    st.session_state.MARGINS = DEFAULT_MARGINS.copy()

def now_iso():
    return dt.datetime.utcnow().isoformat(timespec="seconds")

def read_prices(src: str|None=None) -> pd.DataFrame:
    q = "SELECT source,name,buy,sell,ts FROM prices"
    params = {}
    if src:
        q += " WHERE source=:src"
        params["src"] = src
    q += " ORDER BY datetime(ts) DESC"
    with ENGINE.begin() as con:
        df = pd.read_sql_query(text(q), con, params=params)
    return df

def upsert_prices(df: pd.DataFrame, src: str):
    # kolonları garanti et
    df = df[["name","buy","sell"]].copy()
    df["source"] = src
    df["ts"] = now_iso()
    with ENGINE.begin() as con:
        for _, r in df.iterrows():
            con.execute(text("""
            INSERT INTO prices(source,name, buy, sell, ts)
            VALUES (:source,:name,:buy,:sell,:ts)
            """), r.to_dict())

def last_harem_row(alias_list: list[str]) -> dict|None:
    df = read_prices("HAREM")
    if df.empty: return None
    # ilk eşleşen alias’ı bul
    for nm in alias_list:
        m = df[df["name"] == nm]
        if not m.empty:
            row = m.iloc[0]
            return {"name": nm, "buy": float(row["buy"]), "sell": float(row["sell"]), "ts": row["ts"]}
    return None

def suggest_price(product: str, ttype: str) -> tuple[float|None, dict]:
    info = {"product": product, "ttype": ttype}
    base = last_harem_row(HAREM_ALIAS.get(product, [product]))
    if not base:
        info["reason"] = "HAREM kaydı yok"
        return None, info
    info["matched_name"] = base["name"]
    info["base_sell"] = base["sell"]
    # Öneri: Harem SELL + offset (alış ve satış için farklı)
    offs = st.session_state.MARGINS.get(product, {"buy_offset": 0, "sell_offset": 0})
    if ttype == "Alış":
        price = base["sell"] + float(offs["buy_offset"])
    else:
        price = base["sell"] + float(offs["sell_offset"])
    return round(price, 2), info

def parse_harem_csv(raw: str) -> pd.DataFrame:
    """
    Beklenen biçim (başlıksız satırlar): name,buy,sell
    Türkçe sayıları da destekler: 5.924,87 veya 5924.87
    """
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    rows = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Satır biçimi hatalı: '{ln}' (3 alan beklenir)")
        name, buy_s, sell_s = parts
        def to_float(s: str) -> float:
            s = s.replace(" ", "")
            # 9.516 -> 9516 ; 5.924,87 -> 5924.87 ; 5924,87 -> 5924.87
            s = re.sub(r"\.(?=\d{3}(?:\D|$))", "", s)   # binlik noktaları sil
            s = s.replace(",", ".")
            return float(s)
        rows.append({"name": name, "buy": to_float(buy_s), "sell": to_float(sell_s)})
    return pd.DataFrame(rows)

# ---------- UI ----------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk\n– Entegrasyon")

tabs = st.tabs([
    "📊 Harem Fiyatları", 
    "💱 Alış / Satış", 
    "🏦 Kasa & Envanter"
])

# ========== TAB 1: HAREM ==========
with tabs[0]:
    st.subheader("Harem Fiyatları (CSV yapıştır)")
    st.caption("Biçim: Ad,Alış,Satış  | Örnek:  Gram Altın,5728.68,5807.08  veya  Eski Çeyrek,9.516,9.644")
    raw = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv_input")
    if st.button("Harem İçeri Al", key="btn_harem"):
        try:
            df = parse_harem_csv(raw)
            upsert_prices(df, "HAREM")
            st.success(f"{len(df)} satır kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.write("#### Son Harem Kayıtları")
    st.dataframe(read_prices("HAREM"), use_container_width=True)

# ========== TAB 2: ALIŞ / SATIŞ ==========
with tabs[1]:
    # 10 sn’de bir otomatik tazele (Streamlit v1.38+)
    st.experimental_autorefresh(interval=10_000, key="live_tick_alissat")

    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri, Harem'deki **son satış** satırından hesaplanır (10 sn auto-refresh).")

    colm = st.columns(3)
    product = colm[0].selectbox("Ürün Seç", list(PRODUCTS.keys()), key="trx_product")
    ttype   = colm[1].radio("İşlem Türü", ["Alış", "Satış"], horizontal=True, key="trx_type")
    qty     = colm[2].number_input("Adet / Gram", min_value=0.01, value=1.00, step=0.01, key="trx_qty")

    # Öneri fiyat
    suggestion, info = suggest_price(product, ttype)
    st.write("")  # az boşluk
    colp = st.columns(2)
    man_price = colp[0].number_input("Manuel Birim Fiyat (TL)", 
                                      min_value=0.0, 
                                      value=float(suggestion or 0.0), 
                                      step=0.01, 
                                      key="trx_unit_price")
    total = round(qty * man_price, 2)
    colp[1].metric("Önerilen Fiyat", f"{(suggestion or 0):,.2f} ₺".replace(",", "X").replace(".", ",").replace("X","."))

    st.success(f"Toplam: {total:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X","."))

    # Güvenlik uyarıları
    if suggestion is None:
        st.warning("Harem'de uygun satır bulunamadı. Lütfen önce Harem CSV’sini girin.")
    else:
        # Basit kural: Satış fiyatı, öneri ALIŞ’tan düşük olmasın
        if ttype == "Satış":
            buy_suggestion, _ = suggest_price(product, "Alış")
            if buy_suggestion is not None and man_price < buy_suggestion:
                st.error("⚠️ Satış fiyatı **alış** önerisinin altında olamaz!")
        # Alışta da satış önerisinden yüksekse uyar
        if ttype == "Alış":
            sell_suggestion, _ = suggest_price(product, "Satış")
            if sell_suggestion is not None and man_price > sell_suggestion:
                st.warning("⚠️ Alış fiyatı satış önerisinin **üzerinde** görünüyor.")

    # Not ve Kaydet
    note = st.text_input("Not (opsiyonel)", key="trx_note")
    if st.button("Kaydet", type="primary", key="btn_save_trx"):
        if suggestion is None:
            st.error("Harem fiyatı bulunamadı, işlem kaydedilmedi.")
        else:
            with ENGINE.begin() as con:
                con.execute(text("""
                INSERT INTO transactions(date,product,ttype,unit,qty,unit_price,total,note)
                VALUES (:date,:product,:ttype,:unit,:qty,:unit_price,:total,:note)
                """), {
                    "date": now_iso(),
                    "product": product,
                    "ttype": ttype,
                    "unit": PRODUCTS[product]["unit"],
                    "qty": float(qty),
                    "unit_price": float(man_price),
                    "total": float(total),
                    "note": note or ""
                })
            st.success("İşlem kaydedildi ✅")

    with st.expander("🔎 Fiyat çekim debug"):
        st.json(info)

# ========== TAB 3: KASA & ENVANTER (özet) ==========
with tabs[2]:
    st.subheader("Kasa & Envanter (Özet)")
    # Basit stok hesap: Alış (+), Satış (-) miktar / has mantığı gerekirse genişletiriz
    with ENGINE.begin() as con:
        tx = pd.read_sql_query(text("SELECT date,product,ttype,unit,qty,unit_price,total,note FROM transactions ORDER BY datetime(date) DESC"), con)

    if tx.empty:
        st.info("Henüz işlem yok.")
    else:
        st.write("#### Son İşlemler")
        st.dataframe(tx, use_container_width=True)

        # Ürün bazında miktar özeti
        qty_pivot = tx.pivot_table(index="product",
                                   columns="ttype",
                                   values="qty",
                                   aggfunc="sum",
                                   fill_value=0.0)
        qty_pivot["Net Miktar"] = qty_pivot.get("Alış",0) - qty_pivot.get("Satış",0)
        st.write("#### Ürün Bazında Miktar Özeti")
        st.dataframe(qty_pivot, use_container_width=True)

# ---------- Alt bar: Marj ayarları (isteğe bağlı) ----------
with st.expander("⚙️ Marj Ayarları (Harem SELL + offset)"):
    st.caption("Her ürün için öneri hesaplanırken Harem **Satış** fiyatına bu offset eklenir.")
    for prod in PRODUCTS.keys():
        cols = st.columns(3)
        cols[0].markdown(f"**{prod}**")
        bo = cols[1].number_input("Alış offset", value=float(st.session_state.MARGINS[prod]["buy_offset"]), step=1.0, key=f"bo_{prod}")
        so = cols[2].number_input("Satış offset", value=float(st.session_state.MARGINS[prod]["sell_offset"]), step=1.0, key=f"so_{prod}")
        st.session_state.MARGINS[prod]["buy_offset"]  = float(bo)
        st.session_state.MARGINS[prod]["sell_offset"] = float(so)