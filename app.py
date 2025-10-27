
# app.py — Sarıkaya Kuyumculuk (Harem bazlı) + Mevcut Stok Girişi
import streamlit as st
import pandas as pd
import sqlite3, io, re, datetime as dt
from typing import Optional, Tuple

DB = "data.db"

# ===================== DB =====================
def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS prices(
        source TEXT, name TEXT, buy REAL, sell REAL, ts TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions(
        ts TEXT, product TEXT, ttype TEXT, unit TEXT,
        qty REAL, price REAL, total REAL, note TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS opening_stock(
        ts TEXT, product TEXT, unit TEXT,
        qty REAL, qty_grams REAL, note TEXT
    )""")
    return c

def read_df(q, params=()):
    c = conn()
    df = pd.read_sql_query(q, c, params=params)
    c.close()
    return df

def write_prices(df: pd.DataFrame):
    c = conn()
    df = df.copy()
    df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    df["source"] = "HAREM"
    df[["buy","sell"]] = df[["buy","sell"]].astype(float)
    df[["source","name","buy","sell","ts"]].to_sql("prices", c, if_exists="append", index=False)
    c.commit(); c.close()

def write_tx(product, ttype, unit, qty, price, total, note):
    c = conn()
    c.execute(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
        (dt.datetime.utcnow().isoformat(timespec="seconds"),
         product, ttype, unit, qty, price, total, note)
    )
    c.commit(); c.close()

def write_opening_stock(rows: list[dict]):
    if not rows: return
    c = conn()
    for r in rows:
        c.execute(
            "INSERT INTO opening_stock VALUES (?,?,?,?,?,?)",
            (r["ts"], r["product"], r["unit"], r["qty"], r["qty_grams"], r.get("note",""))
        )
    c.commit(); c.close()

def read_prices_latest(n=100):
    return read_df("SELECT * FROM prices ORDER BY ts DESC LIMIT ?", (n,))

def read_tx():
    return read_df("SELECT * FROM transactions ORDER BY ts DESC")

def read_opening():
    return read_df("SELECT * FROM opening_stock ORDER BY ts DESC")

# ===================== Yardımcılar =====================
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995},
    # istersen buraya 22 ayarları da ekleyebiliriz
}

HAREM_ALIAS = {
    "Çeyrek Altın": ["Eski Çeyrek"],
    "Yarım Altın" : ["Eski Yarım"],
    "Tam Altın"   : ["Eski Tam"],
    "Ata Lira"    : ["Eski Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "Has", "24 Ayar Gram"],
}

def parse_number(x: str) -> float:
    """
    '5.924,87' -> 5924.87
    '5924,87'  -> 5924.87
    '5924.87'  -> 5924.87
    """
    x = str(x).strip()
    if "," in x and "." in x:
        x = x.replace(".", "").replace(",", ".")
    elif "," in x:
        x = x.replace(",", ".")
    return float(x)

def parse_harem_csv(txt: str) -> pd.DataFrame:
    rows = []
    for raw in txt.strip().splitlines():
        if not raw.strip():
            continue
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Satır hatalı: {raw}")
        name, buy, sell = parts
        rows.append({"name": name, "buy": parse_number(buy), "sell": parse_number(sell)})
    return pd.DataFrame(rows)

def last_harem_price(name_variants: list[str]) -> Optional[Tuple[float, float, str]]:
    df = read_prices_latest(200)
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
            return None, {"reason":"Harem 'Gram Altın' yok"}
        _buy, _sell, ts = rec
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

def to_has_grams(product: str, qty: float) -> float:
    meta = PRODUCTS[product]
    if meta["unit"] == "adet":
        return qty * meta["std_weight"] * meta["purity"]
    return qty * meta["purity"]

def inventory_summary() -> pd.DataFrame:
    # açılış + (alış - satış)
    open_df = read_opening()
    tx = read_tx()

    # açılış
    if open_df.empty:
        open_sum = pd.DataFrame(columns=["product","unit","qty_open","has_open"])
    else:
        open_sum = open_df.groupby(["product","unit"], as_index=False).agg(
            qty_open=("qty","sum"),
            has_open=("qty_grams","sum")
        )

    # işlemler
    if tx.empty:
        tx_sum = pd.DataFrame(columns=["product","unit","qty_delta","has_delta"])
    else:
        sign = tx["ttype"].map({"Alış":1,"Satış":-1}).fillna(0)
        tx["qty_delta"] = tx["qty"]*sign
        # has için ürün bilgisi gerekli
        has_list = []
        for _, r in tx.iterrows():
            has_list.append(to_has_grams(r["product"], r["qty"]) * (1 if r["ttype"]=="Alış" else -1))
        tx["has_delta"] = has_list
        tx_sum = tx.groupby(["product","unit"], as_index=False).agg(
            qty_delta=("qty_delta","sum"),
            has_delta=("has_delta","sum")
        )

    # birleştir
    inv = pd.merge(open_sum, tx_sum, how="outer", on=["product","unit"]).fillna(0)
    inv["qty_net"] = inv["qty_open"] + inv["qty_delta"]
    inv["has_net"] = inv["has_open"] + inv["has_delta"]
    inv = inv[["product","unit","qty_open","qty_delta","qty_net","has_open","has_delta","has_net"]]
    return inv.sort_values("product")

def cash_summary() -> float:
    tx = read_tx()
    if tx.empty:
        return 0.0
    sign = tx["ttype"].map({"Alış":-1,"Satış":1}).fillna(0)
    return float((tx["price"]*tx["qty"]*sign).sum())

# ===================== UI =====================
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="centered")
st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

tabs = st.tabs([
    "📊 Harem Fiyatları",
    "📥 Mevcut Stok Girişi (Açılış)",
    "💱 Alış / Satış",
    "🏦 Kasa & Envanter",
])

# --- HAREM ---
with tabs[0]:
    st.subheader("Harem Fiyatları (CSV/Yapıştır)")
    st.caption("Biçim: **Ad,Alış,Satış**  | Ör: `Eski Çeyrek,9516,9644`  `Gram Altın,5728.68,5807.08`")
    ta = st.text_area("CSV'yi buraya yapıştırın", height=150, key="harem_csv_input")
    if st.button("Harem İçeri Al"):
        try:
            df = parse_harem_csv(ta)
            write_prices(df)
            st.success(f"{len(df)} satır kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")
    st.subheader("Son Harem Kayıtları")
    st.dataframe(read_prices_latest(100), use_container_width=True)

# --- AÇILIŞ STOKU ---
with tabs[1]:
    st.subheader("Mevcut Stok Girişi (Açılış) — Kasayı ETKİLEMEZ")
    st.caption("Bu bölüm, işin başlangıcındaki mevcut stoğu tanımlamak içindir. Kasa hesabına yansımaz.")

    # Tek tek giriş
    colA, colB = st.columns(2)
    with colA:
        prod = st.selectbox("Ürün", list(PRODUCTS.keys()), key="open_prod")
        unit = PRODUCTS[prod]["unit"]
        qty_open = st.number_input(f"Miktar ({'Adet' if unit=='adet' else 'Gram'})", min_value=0.00, value=0.00, step=1.0 if unit=="adet" else 0.10, key="open_qty")
        note_open = st.text_input("Not (opsiyonel)", key="open_note")
        if st.button("Açılış Stoğunu Kaydet", key="open_save"):
            rows = [{
                "ts": dt.datetime.utcnow().isoformat(timespec="seconds"),
                "product": prod,
                "unit": unit,
                "qty": float(qty_open),
                "qty_grams": float(to_has_grams(prod, qty_open)),
                "note": note_open or ""
            }]
            write_opening_stock(rows)
            st.success("Açılış stoğu kaydedildi.")

    with colB:
        st.markdown("**CSV ile Toplu Giriş**")
        st.caption("Biçim: `Ürün,Miktar`  | Örnek: `Çeyrek Altın,12`  `24 Ayar Gram,150.5`")
        csv_text = st.text_area("CSV'yi yapıştırın", height=120, key="open_csv")
        if st.button("CSV'den İçeri Al", key="open_csv_btn"):
            try:
                lines = [l for l in csv_text.splitlines() if l.strip()]
                rows = []
                for ln in lines:
                    p, q = [s.strip() for s in ln.split(",", 1)]
                    if p not in PRODUCTS:
                        raise ValueError(f"Ürün tanımsız: {p}")
                    unit = PRODUCTS[p]["unit"]
                    qty = float(parse_number(q))
                    rows.append({
                        "ts": dt.datetime.utcnow().isoformat(timespec="seconds"),
                        "product": p, "unit": unit,
                        "qty": qty, "qty_grams": to_has_grams(p, qty),
                        "note": "CSV import"
                    })
                write_opening_stock(rows)
                st.success(f"{len(rows)} satır açılış stoğu eklendi.")
            except Exception as e:
                st.error(f"Hata: {e}")

    st.subheader("Kayıtlı Açılış Stokları")
    st.dataframe(read_opening(), use_container_width=True)

# --- ALIŞ / SATIŞ ---
with tabs[2]:
    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri Harem’den gelir. Manuel fiyatı değiştirebilirsiniz.")

    product = st.selectbox("Ürün Seç", list(PRODUCTS.keys()), key="trade_prod")
    ttype   = st.radio("İşlem Türü", ["Alış","Satış"], horizontal=True, key="trade_type")
    unit    = PRODUCTS[product]["unit"]
    qty     = st.number_input("Adet / Gram", min_value=0.01, value=1.00, step=1.0 if unit=="adet" else 0.10, key="trade_qty")

    sug, dbg = suggested(product, ttype)
    if sug is None:
        st.warning("Öneri hesaplanamadı. Harem CSV’sini kontrol edin.")
        base_price = 0.0
    else:
        base_price = sug
        st.write(f"**Önerilen Birim Fiyat:** {base_price:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X","."))

    price = st.number_input("Manuel Birim Fiyat (TL)", min_value=0.0, value=float(base_price), step=1.0, key="trade_price")
    total = price * qty
    st.success(f"Toplam: {total:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X","."))

    # Basit güvenlik uyarısı
    if product == "24 Ayar Gram":
        rec = last_harem_price(HAREM_ALIAS["24 Ayar Gram"])
        if rec:
            _, base_sell, _ = rec
            min_buy = base_sell - 20
            min_sell = base_sell + 10
            if ttype=="Alış" and price > min_buy:
                st.error(f"Uyarı: Gram alış fiyatı kuralı aşıyor (≤ {min_buy:.2f}).")
            if ttype=="Satış" and price < min_sell:
                st.error(f"Uyarı: Gram satış fiyatı kuralın altında (≥ {min_sell:.2f}).")
    else:
        coin_buy, info_buy = suggested(product, "Alış")
        if ttype=="Satış" and coin_buy is not None and price < coin_buy:
            st.error(f"Uyarı: Satış fiyatı alışın altında (alış ≈ {coin_buy:.2f}).")

    note = st.text_input("Not (opsiyonel)", key="trade_note")
    if st.button("Kaydet", key="trade_save"):
        write_tx(product, ttype, unit, float(qty), float(price), float(total), note)
        st.success("İşlem kaydedildi.")

    with st.expander("🔎 Fiyat çekim debug"):
        st.json(dbg)

    st.subheader("Son İşlemler")
    st.dataframe(read_tx(), use_container_width=True)

# --- KASA & ENVANTER ---
with tabs[3]:
    st.subheader("Kasa & Envanter")
    st.caption("Envanter = Açılış Stoğu + (Alış − Satış). Kasa yalnızca alış/satıştan etkilenir.")

    inv = inventory_summary()
    if inv.empty:
        st.info("Henüz stok/işlem yok.")
    else:
        st.markdown("### Envanter (adet/gr & has gr)")
        st.dataframe(inv, use_container_width=True)

    st.markdown("### Kasa (TL)")
    kasa = cash_summary()
    st.metric("Kasa Bakiyesi", f"{kasa:,.2f} ₺".replace(",", "X").replace(".", ",").replace("X","."))

    st.markdown("### Açılış Stok Kayıtları")
    st.dataframe(read_opening(), use_container_width=True)
    # --- STOK DÜZELTME ---
with st.expander("🧾 Stok Düzeltme / Güncelleme"):
    st.caption("Bu işlem yalnızca envanteri günceller, kasayı etkilemez.")

    inv_df = inventory_summary()
    if inv_df.empty:
        st.info("Henüz stok yok.")
    else:
        product_list = inv_df["product"].tolist()
        selected_product = st.selectbox("Ürün Seç", product_list)
        current_qty = float(inv_df.loc[inv_df["product"] == selected_product, "qty_net"].values[0])
        st.write(f"**Mevcut stok:** {current_qty:,.2f}")
        new_qty = st.number_input("Yeni stok miktarı", min_value=0.0, value=current_qty, step=0.1)

        if st.button("Stoku Güncelle"):
            diff = new_qty - current_qty
            if diff == 0:
                st.info("Stok aynı, değişiklik yok.")
            else:
                unit = PRODUCTS[selected_product]["unit"]
                has_diff = to_has_grams(selected_product, abs(diff))
                ts = dt.datetime.utcnow().isoformat(timespec="seconds")
                note = f"Stok düzeltme (önce: {current_qty}, sonra: {new_qty})"
                rows = [{
                    "ts": ts,
                    "product": selected_product,
                    "unit": unit,
                    "qty": diff,
                    "qty_grams": has_diff * (1 if diff > 0 else -1),
                    "note": note
                }]
                write_opening_stock(rows)
                st.success(f"{selected_product} stoku {current_qty} → {new_qty} olarak güncellendi.")
                # --- TOPLU STOK GÜNCELLE (CSV/METİN) ---
with st.expander("📥 Toplu Stok Güncelle (CSV / metin ile)", expanded=False):
    st.caption("Biçim: `Ürün Adı, Yeni Stok`  • Örnek: `Çeyrek Altın, 12.5`")
    st.caption("Türkçe ondalık virgül de kabul edilir (örn: 12,5). Her satır bir üründür.")

    # mevcut envanteri çek
    inv_df = inventory_summary()
    if inv_df.empty:
        st.info("Henüz stok yok.")
    else:
        sample = "\n".join([f"{p}, {float(inv_df.loc[inv_df['product']==p,'qty_net'].values[0]):.2f}"
                            for p in inv_df['product'].tolist()[:3]])
        bulk_txt = st.text_area("CSV'yi buraya yapıştırın", value=sample, height=140, key="bulk_csv_text")

        colu, colp = st.columns([1,1])
        uploaded = colu.file_uploader("Veya dosya yükle (.csv / .txt)", type=["csv","txt"], key="bulk_csv_file")
        apply_btn = colp.button("Güncellemeyi Uygula", type="primary", key="bulk_apply_btn")

        # metin + dosyayı birleştir
        raw = ""
        if bulk_txt.strip():
            raw += bulk_txt.strip() + "\n"
        if uploaded is not None:
            raw += uploaded.read().decode("utf-8", errors="ignore")

        # satırları ayrıştır
        def parse_lines(text: str):
            lines = []
            for ln in text.splitlines():
                s = ln.strip()
                if not s:
                    continue
                # ; veya , ayracı destekle
                sep = "," if "," in s else ";"
                try:
                    name, qty = s.split(sep, 1)
                except ValueError:
                    continue
                name = name.strip()
                qty_s = qty.strip().replace(".", "").replace(",", ".")  # 12,5 -> 12.5 ; 1.234,56 -> 1234.56
                try:
                    q = float(qty_s)
                except:
                    continue
                lines.append((name, q))
            return lines

        parsed = parse_lines(raw) if raw else []
        if parsed:
            st.write("Önizleme:")
            st.dataframe(
                pd.DataFrame(parsed, columns=["product", "new_qty"]),
                use_container_width=True, hide_index=True,
            )

        if apply_btn:
            if not parsed:
                st.warning("Geçerli satır bulunamadı.")
            else:
                # ad eşleştirme: en yakın eşleşmeyi bul (tam eşleşme yoksa)
                def best_match(name, choices):
                    exact = [c for c in choices if c.lower() == name.lower()]
                    if exact:
                        return exact[0]
                    # çok basit bir skorlayıcı: alt string / baş harf vs.
                    name_l = name.lower()
                    scored = sorted(choices, key=lambda c: (0 if name_l in c.lower() else 1, abs(len(c)-len(name))))
                    return scored[0]

                product_list = inv_df["product"].tolist()
                ops = []
                for name, new_qty in parsed:
                    matched = best_match(name, product_list)
                    current = float(inv_df.loc[inv_df["product"]==matched,"qty_net"].values[0])
                    diff = new_qty - current
                    if diff == 0:
                        continue
                    unit = PRODUCTS[matched]["unit"]
                    has_diff = to_has_grams(matched, abs(diff))
                    rows = [{
                        "ts": dt.datetime.utcnow().isoformat(timespec="seconds"),
                        "product": matched,
                        "unit": unit,
                        "qty": diff,
                        "qty_grams": has_diff * (1 if diff > 0 else -1),
                        "note": f"Toplu stok düzeltme (önce: {current}, sonra: {new_qty})"
                    }]
                    ops.append({"product": matched, "before": current, "after": new_qty, "diff": diff})

                    # envanter düzeltmesini opening_stock'a yaz
                    write_opening_stock(rows)

                if ops:
                    st.success(f"{len(ops)} ürün güncellendi.")
                    st.dataframe(pd.DataFrame(ops), use_container_width=True, hide_index=True)
                else:
                    st.info("Değişiklik gerektiren satır yoktu.")
                    # --- STOK HAREKET GEÇMİŞİ ---
with st.expander("📜 Stok Hareket Geçmişi", expanded=False):
    st.caption("Açılış/ düzeltme hareketleri ve işlemler (alış/satış) bir arada gösterilir.")

    # veriyi çek
    def _safe_read(tbl):
        try:
            return read_sql(tbl)
        except Exception:
            return pd.DataFrame()

    tx = _safe_read("transactions")           # beklenen kolonlar: ts, product, ttype, unit, qty, qty_grams, note
    op = _safe_read("opening_stock")          # beklenen kolonlar: ts, product, unit, qty, qty_grams, note

    # normalize alanlar
    if not op.empty:
        op = op.copy()
        op["ttype"] = op.get("ttype", "Düzeltme")
    if not tx.empty:
        tx = tx.copy()
        tx["ttype"] = tx["ttype"].fillna("İşlem")

    all_df = pd.concat([op, tx], ignore_index=True) if not (op.empty and tx.empty) else pd.DataFrame()
    if all_df.empty:
        st.info("Henüz hareket kaydı yok.")
    else:
        # tarih biçimle
        all_df["ts"] = pd.to_datetime(all_df["ts"], errors="coerce")
        all_df = all_df.sort_values("ts", ascending=False)

        # filtreler
        products = ["(Tümü)"] + sorted(all_df["product"].dropna().unique().tolist())
        sel_prod = st.selectbox("Ürün", products, key="hist_prod")
        today = dt.date.today()
        d1, d2 = st.date_input(
            "Tarih aralığı",
            value=(today - dt.timedelta(days=14), today),
            key="hist_dates"
        )
        f = all_df
        if sel_prod != "(Tümü)":
            f = f[f["product"] == sel_prod]
        if isinstance(d1, dt.date) and isinstance(d2, dt.date):
            start = dt.datetime.combine(d1, dt.time.min)
            end   = dt.datetime.combine(d2, dt.time.max)
            f = f[(f["ts"] >= start) & (f["ts"] <= end)]

        # özet
        col1, col2 = st.columns(2)
        col1.metric("Toplam Miktar (qty)", f["qty"].sum() if "qty" in f else 0)
        col2.metric("Toplam Has (gr)", f["qty_grams"].sum() if "qty_grams" in f else 0)

        # tablo
        show_cols = [c for c in ["ts","ttype","product","unit","qty","qty_grams","note"] if c in f.columns]
        st.dataframe(f[show_cols], use_container_width=True, hide_index=True)