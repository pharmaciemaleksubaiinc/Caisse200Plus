# caisse200_plus.py
import os, json, hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ================== CONFIG ==================
st.set_page_config(page_title="Registre — Caisse & Boîte", layout="wide")
TZ = ZoneInfo("America/Toronto")

DATA_DIR = "data"
RECORDS_DIR = os.path.join(DATA_DIR, "records")
os.makedirs(RECORDS_DIR, exist_ok=True)

# ================== DENOMS ==================
DENOMS = {
    "Billet 100 $": 10000,
    "Billet 50 $": 5000,
    "Billet 20 $": 2000,
    "Billet 10 $": 1000,
    "Billet 5 $": 500,
    "Pièce 2 $": 200,
    "Pièce 1 $": 100,
    "Pièce 0,25 $": 25,
    "Pièce 0,10 $": 10,
    "Pièce 0,05 $": 5,
    "Rouleau 2 $ (25) — 50 $": 5000,
    "Rouleau 1 $ (25) — 25 $": 2500,
    "Rouleau 0,25 $ (40) — 10 $": 1000,
    "Rouleau 0,10 $ (50) — 5 $": 500,
    "Rouleau 0,05 $ (40) — 2 $": 200,
}

BILLS_BIG = ["Billet 100 $", "Billet 50 $", "Billet 20 $"]
BILLS_SMALL = ["Billet 10 $", "Billet 5 $"]
COINS = ["Pièce 2 $", "Pièce 1 $", "Pièce 0,25 $", "Pièce 0,10 $", "Pièce 0,05 $"]
ROLLS = [
    "Rouleau 2 $ (25) — 50 $",
    "Rouleau 1 $ (25) — 25 $",
    "Rouleau 0,25 $ (40) — 10 $",
    "Rouleau 0,10 $ (50) — 5 $",
    "Rouleau 0,05 $ (40) — 2 $",
]
DISPLAY_ORDER = BILLS_BIG + BILLS_SMALL + COINS + ROLLS

# ================== PATHS ==================
def day_key(d: date) -> str:
    return d.isoformat()

def state_path(d: date) -> str:
    return os.path.join(RECORDS_DIR, f"{day_key(d)}_state.json")

def receipt_path(d: date) -> str:
    return os.path.join(RECORDS_DIR, f"{day_key(d)}_receipt.html")

# ================== SMALL UTILS ==================
def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def cents_to_str(c: int) -> str:
    return f"{c/100:.2f} $"

def total_cents(counts: dict) -> int:
    return sum(safe_int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)

def sub_counts(a: dict, b: dict) -> dict:
    return {k: safe_int(a.get(k, 0)) - safe_int(b.get(k, 0)) for k in DENOMS}

def add_counts(a: dict, b: dict) -> dict:
    return {k: safe_int(a.get(k, 0)) + safe_int(b.get(k, 0)) for k in DENOMS}

def clamp_locked(locked: dict, avail: dict) -> dict:
    out = {}
    for k, v in locked.items():
        v = safe_int(v, 0)
        if v < 0:
            v = 0
        mx = safe_int(avail.get(k, 0), 0)
        if v > mx:
            v = mx
        out[k] = v
    return out

def take_greedy(remaining: int, keys: list, avail: dict, out: dict, locked: dict) -> int:
    for k in keys:
        if remaining <= 0:
            break
        if k in locked:
            continue
        v = DENOMS[k]
        can_take = safe_int(avail.get(k, 0)) - safe_int(out.get(k, 0))
        if can_take < 0:
            can_take = 0
        take = min(remaining // v, can_take)
        if take > 0:
            out[k] = safe_int(out.get(k, 0)) + take
            remaining -= take * v
    return remaining

def suggest(amount_cents: int, allowed: list, avail: dict, locked: dict, priority: list):
    out = {k: 0 for k in DENOMS}
    for k, q in locked.items():
        out[k] = safe_int(q)

    remaining = amount_cents - total_cents(out)
    if remaining < 0:
        return out, remaining

    allowed_set = set(allowed)
    keys = [k for k in priority if k in allowed_set]
    remaining = take_greedy(remaining, keys, avail, out, locked)
    return out, remaining

def suggest_retrait_caisse(diff_cents: int, allowed: list, avail: dict, locked: dict):
    coins_desc = sorted(COINS, key=lambda x: DENOMS[x], reverse=True)
    rolls_desc = sorted(ROLLS, key=lambda x: DENOMS[x], reverse=True)
    priority = BILLS_BIG + BILLS_SMALL + coins_desc + rolls_desc
    return suggest(diff_cents, allowed, avail, locked, priority)

def suggest_changebox(amount_cents: int, allowed: list, avail: dict, locked: dict):
    coins_desc = sorted(COINS, key=lambda x: DENOMS[x], reverse=True)
    rolls_desc = sorted(ROLLS, key=lambda x: DENOMS[x], reverse=True)
    priority = coins_desc + rolls_desc + BILLS_SMALL + BILLS_BIG
    return suggest(amount_cents, allowed, avail, locked, priority)

def hash_payload(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

# ================== STORAGE ==================
def load_day(d: date):
    p = state_path(d)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_day(d: date, payload: dict):
    with open(state_path(d), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def build_receipt_html(payload: dict) -> str:
    meta = payload.get("meta", {})
    reg_rows = payload.get("register", {}).get("rows_calc", [])
    box_rows = payload.get("changebox", {}).get("rows_calc", [])

    meta_html = "".join([f"<div><b>{k}:</b> {v}</div>" for k, v in meta.items()])

    def table_block(title, headers, rows):
        thead = "".join([f"<th>{h}</th>" for h in headers])
        body = ""
        for r in rows:
            body += "<tr>" + "".join([f"<td>{r.get(h,'')}</td>" for h in headers]) + "</tr>"
        return f"""
        <div class="section">
          <h3>{title}</h3>
          <table>
            <thead><tr>{thead}</tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
        """

    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Reçu — Registre</title>
        <style>
          body {{ font-family: Arial, sans-serif; padding: 18px; color:#111; }}
          .top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }}
          .meta {{ font-size: 13px; opacity: 0.95; }}
          .section {{ margin-top: 18px; }}
          h2 {{ margin:0; }}
          h3 {{ margin: 0 0 8px 0; }}
          table {{ width:100%; border-collapse: collapse; font-size: 13px; }}
          th, td {{ border: 1px solid #222; padding: 6px; }}
          th {{ background: #f0f0f0; }}
          .btnbar {{ margin-top: 12px; display:flex; gap:10px; }}
          button {{ padding:10px 14px; border-radius:10px; border:1px solid #bbb; background:#fff; cursor:pointer; font-weight:700; }}
          .note {{ font-size:12px; opacity:0.7; margin-top:6px; }}
          @media print {{
            .btnbar {{ display:none; }}
            body {{ padding:0; }}
          }}
        </style>
      </head>
      <body>
        <div class="top">
          <div>
            <h2>Registre — Caisse & Boîte de monnaie</h2>
            <div class="note">Imprime avec le bouton (ou Ctrl+P / Cmd+P).</div>
          </div>
          <div class="meta">{meta_html}</div>
        </div>

        <div class="btnbar">
          <button onclick="window.print()">🖨️ Imprimer le reçu</button>
        </div>

        {table_block("Caisse — Calcul", ["Dénomination","OPEN","CLOSE","RETRAIT","RESTANT"], reg_rows)}
        {table_block("Boîte de monnaie — Calcul", ["Dénomination","Boîte (actuel)","Vers boîte","Depuis boîte","Boîte (après)"], box_rows)}
      </body>
    </html>
    """

def save_receipt(d: date, payload: dict):
    with open(receipt_path(d), "w", encoding="utf-8") as f:
        f.write(build_receipt_html(payload))

def list_saved_days():
    files = sorted([f for f in os.listdir(RECORDS_DIR) if f.endswith("_state.json")])
    return [f.replace("_state.json", "") for f in files]

# ================== DF HELPERS ==================
def default_register_df():
    return pd.DataFrame([{
        "Dénomination": k,
        "OPEN": 0,
        "CLOSE": 0,
        "Autorisé retrait": True
    } for k in DISPLAY_ORDER])

def default_box_df():
    return pd.DataFrame([{
        "Dénomination": k,
        "Boîte (actuel)": 0,
        "Autorisé boîte": True
    } for k in DISPLAY_ORDER])

def df_to_counts(df: pd.DataFrame, col: str) -> dict:
    return {r["Dénomination"]: safe_int(r.get(col, 0), 0) for _, r in df.iterrows()}

def df_allowed(df: pd.DataFrame, col_allow: str) -> list:
    out = []
    for _, r in df.iterrows():
        if bool(r.get(col_allow, False)):
            out.append(r["Dénomination"])
    return out

# ================== STATE INITIALISATION (THE FIX) ==================
def ensure_state():
    # Always define the keys, every run. No surprises.
    defaults = {
        "cashier": "",
        "register_no": 1,
        "target_dollars": 200,
        "box_target_dollars": 0,
        "df_register": default_register_df(),
        "df_box": default_box_df(),
        "locked_retrait": {},
        "locked_to_box": {},
        "locked_from_box": {},
        "last_saved_hash": None,
        "last_saved_at": None,
        "_retrait_counts": {k: 0 for k in DENOMS},
        "_restant_counts": {k: 0 for k in DENOMS},
        "_box_now": {k: 0 for k in DENOMS},
        "_box_to": {k: 0 for k in DENOMS},
        "_box_from": {k: 0 for k in DENOMS},
        "_box_after": {k: 0 for k in DENOMS},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

ensure_state()

# ================== DAILY LOAD (SAFE) ==================
today = datetime.now(TZ).date()

if "booted_for" not in st.session_state or st.session_state.booted_for != today:
    st.session_state.booted_for = today
    existing = load_day(today)

    if existing:
        st.session_state.cashier = existing.get("meta", {}).get("Caissier(ère)", st.session_state.cashier)
        st.session_state.register_no = safe_int(existing.get("meta", {}).get("Caisse #", st.session_state.register_no), 1)
        st.session_state.target_dollars = safe_int(existing.get("meta", {}).get("Cible $", st.session_state.target_dollars), 200)
        st.session_state.box_target_dollars = safe_int(existing.get("meta", {}).get("Cible boîte $", st.session_state.box_target_dollars), 0)

        reg_table = existing.get("register", {}).get("table")
        if isinstance(reg_table, list) and len(reg_table) > 0:
            st.session_state.df_register = pd.DataFrame(reg_table)

        box_table = existing.get("changebox", {}).get("table")
        if isinstance(box_table, list) and len(box_table) > 0:
            st.session_state.df_box = pd.DataFrame(box_table)

        st.session_state.locked_retrait = existing.get("register", {}).get("locked_retrait", {})
        st.session_state.locked_to_box = existing.get("changebox", {}).get("locked_to_box", {})
        st.session_state.locked_from_box = existing.get("changebox", {}).get("locked_from_box", {})

# ================== AUTH (again, after state init) ==================
if "auth" not in st.session_state:
    st.session_state.auth = False
if not st.session_state.auth:
    st.title("Accès protégé")
    pwd = st.text_input("Mot de passe", type="password")
    app_password = st.secrets.get("APP_PASSWORD")
    if st.button("Se connecter"):
        if not app_password:
            st.error("APP_PASSWORD manquant dans Streamlit secrets.")
            st.stop()
        if pwd == app_password:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()

# ================== HEADER ==================
st.title("Registre quotidien — Caisse & Boîte de monnaie")

top = st.columns([1.2, 1.1, 1.2, 1.8, 2.2])
with top[0]:
    st.write("**Date:**", today.isoformat())
with top[1]:
    st.write("**Heure:**", datetime.now(TZ).strftime("%H:%M"))
with top[2]:
    st.session_state.register_no = st.selectbox("Caisse #", [1, 2, 3], index=[1,2,3].index(st.session_state.register_no))
with top[3]:
    st.session_state.cashier = st.text_input("Caissier(ère)", value=st.session_state.cashier)
with top[4]:
    st.session_state.target_dollars = st.number_input("Cible à laisser ($)", min_value=0, step=10, value=int(st.session_state.target_dollars))

st.divider()

# ================== QUICK ADJUST (NO FORMS) ==================
st.subheader("Ajustement rapide (➖/➕)")
qa1, qa2, qa3, qa4, qa5 = st.columns([1.6, 2.5, 1.2, 1.3, 3.4])

with qa1:
    qa_table = st.selectbox("Table", ["Caisse: OPEN", "Caisse: CLOSE", "Boîte: actuel"], index=1)
with qa2:
    qa_denom = st.selectbox("Dénomination", DISPLAY_ORDER, index=0)
with qa3:
    qa_step = st.selectbox("Pas", [1, 5, 10], index=0)
with qa4:
    m = st.button("➖", key="qa_minus")
    p = st.button("➕", key="qa_plus")
with qa5:
    st.caption("Ça modifie les tableaux directement, sans casser la saisie.")

if m or p:
    if qa_table.startswith("Caisse"):
        col = "OPEN" if "OPEN" in qa_table else "CLOSE"
        df = st.session_state.df_register.copy()
        idx = df.index[df["Dénomination"] == qa_denom]
        if len(idx) == 1:
            i = idx[0]
            cur = safe_int(df.loc[i, col], 0)
            df.loc[i, col] = max(0, cur - qa_step) if m else (cur + qa_step)
        st.session_state.df_register = df
    else:
        col = "Boîte (actuel)"
        df = st.session_state.df_box.copy()
        idx = df.index[df["Dénomination"] == qa_denom]
        if len(idx) == 1:
            i = idx[0]
            cur = safe_int(df.loc[i, col], 0)
            df.loc[i, col] = max(0, cur - qa_step) if m else (cur + qa_step)
        st.session_state.df_box = df

    st.rerun()

st.divider()

# ================== ROW BUILDERS ==================
def rows_caisse(open_c, close_c, retrait_c, restant_c):
    rows = []
    for k in DISPLAY_ORDER:
        rows.append({
            "Dénomination": k,
            "OPEN": safe_int(open_c.get(k, 0)),
            "CLOSE": safe_int(close_c.get(k, 0)),
            "RETRAIT": safe_int(retrait_c.get(k, 0)),
            "RESTANT": safe_int(restant_c.get(k, 0)),
        })
    rows.append({
        "Dénomination": "TOTAL ($)",
        "OPEN": f"{total_cents(open_c)/100:.2f}",
        "CLOSE": f"{total_cents(close_c)/100:.2f}",
        "RETRAIT": f"{total_cents(retrait_c)/100:.2f}",
        "RESTANT": f"{total_cents(restant_c)/100:.2f}",
    })
    return rows

def rows_box(box_now, to_box, from_box, box_after):
    rows = []
    for k in DISPLAY_ORDER:
        rows.append({
            "Dénomination": k,
            "Boîte (actuel)": safe_int(box_now.get(k, 0)),
            "Vers boîte": safe_int(to_box.get(k, 0)),
            "Depuis boîte": safe_int(from_box.get(k, 0)),
            "Boîte (après)": safe_int(box_after.get(k, 0)),
        })
    rows.append({
        "Dénomination": "TOTAL ($)",
        "Boîte (actuel)": f"{total_cents(box_now)/100:.2f}",
        "Vers boîte": f"{total_cents(to_box)/100:.2f}",
        "Depuis boîte": f"{total_cents(from_box)/100:.2f}",
        "Boîte (après)": f"{total_cents(box_after)/100:.2f}",
    })
    return rows

# ================== TABS ==================
tab1, tab2, tab3 = st.tabs(["1) Caisse", "2) Boîte de monnaie", "3) Historique / Reçus"])

with tab1:
    st.subheader("Caisse — Comptage (OPEN/CLOSE) + Autorisés")

    with st.form("form_register"):
        edited = st.data_editor(
            st.session_state.df_register,
            hide_index=True,
            use_container_width=True,
            height=560,
            column_config={
                "Dénomination": st.column_config.TextColumn(disabled=True),
                "OPEN": st.column_config.NumberColumn(min_value=0, step=1),
                "CLOSE": st.column_config.NumberColumn(min_value=0, step=1),
                "Autorisé retrait": st.column_config.CheckboxColumn(),
            },
            key="editor_register",
        )
        submit_reg = st.form_submit_button("✅ Enregistrer le comptage Caisse")

    if submit_reg:
        st.session_state.df_register = edited

    open_counts = df_to_counts(st.session_state.df_register, "OPEN")
    close_counts = df_to_counts(st.session_state.df_register, "CLOSE")
    allowed_retrait = df_allowed(st.session_state.df_register, "Autorisé retrait")

    colA, colB, colC = st.columns(3)
    colA.info("TOTAL OPEN: " + cents_to_str(total_cents(open_counts)))
    total_close = total_cents(close_counts)
    colB.success("TOTAL CLOSE: " + cents_to_str(total_close))

    TARGET = int(st.session_state.target_dollars) * 100
    diff = total_close - TARGET
    colC.write("**À retirer:** " + f"**{cents_to_str(diff)}**")

    st.divider()
    st.subheader("RETRAIT — Proposition + verrouillage")

    if st.button("Reset verrouillage retrait"):
        st.session_state.locked_retrait = {}
        st.rerun()

    retrait_counts = {k: 0 for k in DENOMS}
    restant_counts = dict(close_counts)

    if diff <= 0:
        st.warning("Sous la cible (ou égal).")
    elif not allowed_retrait:
        st.error("Aucun type autorisé.")
    else:
        st.session_state.locked_retrait = clamp_locked(st.session_state.locked_retrait, close_counts)
        retrait_counts, remaining = suggest_retrait_caisse(diff, allowed_retrait, close_counts, dict(st.session_state.locked_retrait))

        if remaining == 0:
            st.success("Retrait proposé: " + cents_to_str(total_cents(retrait_counts)))
        else:
            st.warning("Reste non couvert: " + cents_to_str(remaining))

        restant_counts = sub_counts(close_counts, retrait_counts)
        st.info("RESTANT: " + cents_to_str(total_cents(restant_counts)))

    st.session_state._retrait_counts = retrait_counts
    st.session_state._restant_counts = restant_counts

with tab2:
    st.subheader("Boîte — état actuel + mouvements")
    st.session_state.box_target_dollars = st.number_input("Cible boîte ($)", min_value=0, step=10, value=int(st.session_state.box_target_dollars))

    with st.form("form_box"):
        edited_box = st.data_editor(
            st.session_state.df_box,
            hide_index=True,
            use_container_width=True,
            height=560,
            column_config={
                "Dénomination": st.column_config.TextColumn(disabled=True),
                "Boîte (actuel)": st.column_config.NumberColumn(min_value=0, step=1),
                "Autorisé boîte": st.column_config.CheckboxColumn(),
            },
            key="editor_box",
        )
        submit_box = st.form_submit_button("✅ Enregistrer le comptage Boîte")

    if submit_box:
        st.session_state.df_box = edited_box

    box_now = df_to_counts(st.session_state.df_box, "Boîte (actuel)")
    allowed_box = df_allowed(st.session_state.df_box, "Autorisé boîte")

    total_box = total_cents(box_now)
    st.info("TOTAL boîte (actuel): " + cents_to_str(total_box))

    box_target = int(st.session_state.box_target_dollars) * 100
    delta = box_target - total_box

    register_avail = st.session_state._restant_counts
    st.write("Dispo côté caisse (RESTANT): **" + cents_to_str(total_cents(register_avail)) + "**")
    st.write("Écart (cible - boîte): **" + cents_to_str(delta) + "**")

    if st.button("Reset verrouillage boîte"):
        st.session_state.locked_to_box = {}
        st.session_state.locked_from_box = {}
        st.rerun()

    to_box = {k: 0 for k in DENOMS}
    from_box = {k: 0 for k in DENOMS}

    if not allowed_box:
        st.error("Aucun type autorisé pour la boîte.")
    else:
        if delta > 0:
            st.session_state.locked_to_box = clamp_locked(st.session_state.locked_to_box, register_avail)
            to_box, rem = suggest_changebox(delta, allowed_box, register_avail, dict(st.session_state.locked_to_box))
            st.write("Vers boîte:", cents_to_str(total_cents(to_box)), "Reste:", cents_to_str(rem))
        elif delta < 0:
            need = -delta
            st.session_state.locked_from_box = clamp_locked(st.session_state.locked_from_box, box_now)
            from_box, rem = suggest_changebox(need, allowed_box, box_now, dict(st.session_state.locked_from_box))
            st.write("Depuis boîte:", cents_to_str(total_cents(from_box)), "Reste:", cents_to_str(rem))
        else:
            st.success("Boîte déjà à la cible.")

    box_after = add_counts(sub_counts(box_now, from_box), to_box)
    st.info("TOTAL boîte (après): " + cents_to_str(total_cents(box_after)))

    st.session_state._box_now = box_now
    st.session_state._box_to = to_box
    st.session_state._box_from = from_box
    st.session_state._box_after = box_after

# ================== SAVE + RECEIPT ==================
st.divider()
st.subheader("Sauvegarde & reçu imprimable")

meta = {
    "Date": today.isoformat(),
    "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    "Caisse #": int(st.session_state.register_no),
    "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
    "Cible $": int(st.session_state.target_dollars),
    "Cible boîte $": int(st.session_state.box_target_dollars),
    "Note": "Enregistré automatiquement par date.",
}

payload = {
    "meta": meta,
    "register": {
        "table": st.session_state.df_register.to_dict("records"),
        "locked_retrait": st.session_state.locked_retrait,
        "rows_calc": rows_caisse(
            df_to_counts(st.session_state.df_register, "OPEN"),
            df_to_counts(st.session_state.df_register, "CLOSE"),
            st.session_state._retrait_counts,
            st.session_state._restant_counts,
        ),
    },
    "changebox": {
        "table": st.session_state.df_box.to_dict("records"),
        "locked_to_box": st.session_state.locked_to_box,
        "locked_from_box": st.session_state.locked_from_box,
        "rows_calc": rows_box(
            st.session_state._box_now,
            st.session_state._box_to,
            st.session_state._box_from,
            st.session_state._box_after,
        ),
    },
}

def autosave(payload: dict):
    h = hash_payload(payload)
    if st.session_state.last_saved_hash == h:
        return
    save_day(today, payload)
    save_receipt(today, payload)
    st.session_state.last_saved_hash = h
    st.session_state.last_saved_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

autosave(payload)

s1, s2 = st.columns([1.5, 4.5])
with s1:
    if st.button("💾 Enregistrer maintenant"):
        save_day(today, payload)
        save_receipt(today, payload)
        st.session_state.last_saved_hash = hash_payload(payload)
        st.session_state.last_saved_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
with s2:
    st.write("Dernière sauvegarde:", st.session_state.last_saved_at or "—")

st.markdown("### Reçu imprimable (aperçu)")
components.html(build_receipt_html(payload), height=720, scrolling=True)

with tab3:
    st.subheader("Historique / Reçus")
    days = list_saved_days()
    if not days:
        st.info("Aucun enregistrement trouvé.")
    else:
        pick = st.selectbox("Choisir une date", days, index=len(days) - 1)
        d = date.fromisoformat(pick)
        loaded = load_day(d)

        if not loaded:
            st.error("Impossible de charger.")
        else:
            st.json(loaded.get("meta", {}))
            htmlp = receipt_path(d)
            if os.path.exists(htmlp):
                with open(htmlp, "r", encoding="utf-8") as f:
                    components.html(f.read(), height=720, scrolling=True)

                c1, c2 = st.columns(2)
                with c1:
                    with open(htmlp, "rb") as f:
                        st.download_button("⬇️ Télécharger le reçu HTML", f, file_name=os.path.basename(htmlp), mime="text/html")
                with c2:
                    sp = state_path(d)
                    with open(sp, "rb") as f:
                        st.download_button("⬇️ Télécharger l’état JSON", f, file_name=os.path.basename(sp), mime="application/json")
            else:
                st.warning("Reçu manquant pour cette date.")

