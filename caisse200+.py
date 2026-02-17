# caisse200plus_app.py
import os, json, hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ================== CONFIG ==================
st.set_page_config(page_title="Registre — Caisse & Boîte", layout="wide")
TZ = ZoneInfo("America/Toronto")

BASE_DIR = "data"
DIR_CAISSE = os.path.join(BASE_DIR, "records_caisse")
DIR_BOITE = os.path.join(BASE_DIR, "records_boite")
os.makedirs(DIR_CAISSE, exist_ok=True)
os.makedirs(DIR_BOITE, exist_ok=True)

# ================== AUTH ==================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("Accès protégé")
    pwd = st.text_input("Mot de passe", type="password", key="pwd")
    app_password = st.secrets.get("APP_PASSWORD")
    if st.button("Se connecter", key="btn_login"):
        if not app_password:
            st.error("APP_PASSWORD manquant dans Streamlit secrets.")
            st.stop()
        if pwd == app_password:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()

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

# ================== HELPERS ==================
def safe_int(x, default=0):
    try:
        if x is None or x == "":
            return default
        return int(x)
    except Exception:
        return default

def cents_to_str(c: int) -> str:
    return f"{c/100:.2f} $"

def total_cents(counts: dict) -> int:
    return sum(safe_int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)

def add_counts(a: dict, b: dict) -> dict:
    return {k: safe_int(a.get(k, 0)) + safe_int(b.get(k, 0)) for k in DENOMS}

def sub_counts(a: dict, b: dict) -> dict:
    return {k: safe_int(a.get(k, 0)) - safe_int(b.get(k, 0)) for k in DENOMS}

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

def suggest_withdrawal(amount_cents: int, avail: dict, locked: dict, priority: list):
    """
    Returns (withdraw_counts, remaining_cents).
    - withdraw_counts respects availability and locked counts.
    """
    out = {k: 0 for k in DENOMS}
    for k, q in locked.items():
        out[k] = safe_int(q)

    remaining = amount_cents - total_cents(out)
    if remaining < 0:
        return out, remaining

    remaining = take_greedy(remaining, priority, avail, out, locked)
    return out, remaining

def hash_payload(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def list_dates(folder: str):
    files = sorted([f for f in os.listdir(folder) if f.endswith("_state.json")])
    return [f.replace("_state.json", "") for f in files]

def save_state(folder: str, d: date, payload: dict):
    p = os.path.join(folder, f"{d.isoformat()}_state.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def load_state(folder: str, d: date):
    p = os.path.join(folder, f"{d.isoformat()}_state.json")
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_receipt(folder: str, d: date, html: str):
    p = os.path.join(folder, f"{d.isoformat()}_receipt.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)

def load_receipt(folder: str, d: date):
    p = os.path.join(folder, f"{d.isoformat()}_receipt.html")
    if not os.path.exists(p):
        return None, None
    with open(p, "r", encoding="utf-8") as f:
        return f.read(), p

# ================== RECEIPTS ==================
def receipt_html(title: str, meta: dict, headers: list, rows: list) -> str:
    meta_html = "".join([f"<div><b>{k}:</b> {v}</div>" for k, v in meta.items()])
    thead = "".join([f"<th>{h}</th>" for h in headers])

    body = ""
    for r in rows:
        body += "<tr>" + "".join([f"<td style='text-align:center'>{r.get(h,'')}</td>" if h != headers[0] else f"<td>{r.get(h,'')}</td>" for h in headers]) + "</tr>"

    return f"""
    <html><head><meta charset="utf-8"/><title>{title}</title>
    <style>
      body{{font-family:Arial,sans-serif;padding:18px;color:#111}}
      .top{{display:flex;justify-content:space-between;gap:16px}}
      .meta{{font-size:13px;opacity:.95}}
      table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:14px}}
      th,td{{border:1px solid #222;padding:6px}}
      th{{background:#f0f0f0}}
      .btnbar{{margin-top:12px}}
      button{{padding:10px 14px;border-radius:10px;border:1px solid #bbb;background:#fff;cursor:pointer;font-weight:700}}
      @media print{{.btnbar{{display:none}} body{{padding:0}}}}
    </style></head>
    <body>
      <div class="top">
        <div><h2 style="margin:0">{title}</h2><div style="opacity:.7;font-size:12px">Imprime avec le bouton.</div></div>
        <div class="meta">{meta_html}</div>
      </div>
      <div class="btnbar"><button onclick="window.print()">🖨️ Imprimer</button></div>
      <table>
        <thead><tr>{thead}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </body></html>
    """

# ================== DEFAULT TABLES ==================
def df_register_default():
    return pd.DataFrame([{"Dénomination": k, "OPEN": 0, "CLOSE": 0} for k in DISPLAY_ORDER])

def df_box_before_default():
    return pd.DataFrame([{"Dénomination": k, "Boîte (avant)": 0} for k in DISPLAY_ORDER])

def df_box_deposit_default():
    # Keep simple: allow any denom, you can restrict later.
    return pd.DataFrame([{"Dénomination": k, "Dépôt": 0} for k in DISPLAY_ORDER])

# ================== SESSION INIT ==================
today = datetime.now(TZ).date()

def ensure_state():
    defaults = {
        "booted_for": None,
        "cashier": "",
        "register_no": 1,
        "target_dollars": 200,

        "df_register": df_register_default(),
        "df_box_before": df_box_before_default(),
        "df_box_deposit": df_box_deposit_default(),

        "locked_retrait_caisse": {},
        "locked_withdraw_boite": {},

        "last_hash_caisse": None,
        "last_hash_boite": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

ensure_state()

# Load today's saved states once per day
if st.session_state.booted_for != today:
    st.session_state.booted_for = today

    sc = load_state(DIR_CAISSE, today)
    if sc:
        st.session_state.cashier = sc.get("meta", {}).get("Caissier(ère)", st.session_state.cashier)
        st.session_state.register_no = safe_int(sc.get("meta", {}).get("Caisse #", st.session_state.register_no), 1)
        st.session_state.target_dollars = safe_int(sc.get("meta", {}).get("Cible $", st.session_state.target_dollars), 200)
        table = sc.get("register", {}).get("table", None)
        if isinstance(table, list) and table:
            st.session_state.df_register = pd.DataFrame(table)
        st.session_state.locked_retrait_caisse = sc.get("register", {}).get("locked_retrait", {})

    sb = load_state(DIR_BOITE, today)
    if sb:
        st.session_state.cashier = sb.get("meta", {}).get("Caissier(ère)", st.session_state.cashier)
        tb = sb.get("boite", {}).get("table_before", None)
        td = sb.get("boite", {}).get("table_deposit", None)
        if isinstance(tb, list) and tb:
            st.session_state.df_box_before = pd.DataFrame(tb)
        if isinstance(td, list) and td:
            st.session_state.df_box_deposit = pd.DataFrame(td)
        st.session_state.locked_withdraw_boite = sb.get("boite", {}).get("locked_withdraw", {})

# ================== HEADER ==================
st.title("Registre — Caisse & Boîte de monnaie")

h1, h2, h3, h4, h5 = st.columns([1.2, 1.0, 1.2, 1.8, 2.0])
with h1:
    st.write("**Date:**", today.isoformat())
with h2:
    st.write("**Heure:**", datetime.now(TZ).strftime("%H:%M"))
with h3:
    st.session_state.register_no = st.selectbox("Caisse #", [1, 2, 3], index=[1, 2, 3].index(st.session_state.register_no), key="sel_register")
with h4:
    st.session_state.cashier = st.text_input("Caissier(ère)", value=st.session_state.cashier, key="txt_cashier")
with h5:
    st.session_state.target_dollars = st.number_input("Cible à laisser ($)", min_value=0, step=10, value=int(st.session_state.target_dollars), key="num_target")

st.divider()

# ================== TABS ==================
tab_caisse, tab_boite, tab_save = st.tabs(["1) Caisse", "2) Boîte (Échange)", "3) Sauvegarde & reçus"])

# ================== TAB: CAISSE ==================
with tab_caisse:
    st.subheader("Caisse — Comptage OPEN/CLOSE (saisie stable, aucun reset)")

    edited = st.data_editor(
        st.session_state.df_register,
        hide_index=True,
        use_container_width=True,
        height=520,
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "OPEN": st.column_config.NumberColumn(min_value=0, step=1),
            "CLOSE": st.column_config.NumberColumn(min_value=0, step=1),
        },
        key="ed_register",
    )
    # Always keep latest (this avoids “type twice” + no submit nonsense)
    st.session_state.df_register = edited

    open_counts = {r["Dénomination"]: safe_int(r["OPEN"]) for _, r in st.session_state.df_register.iterrows()}
    close_counts = {r["Dénomination"]: safe_int(r["CLOSE"]) for _, r in st.session_state.df_register.iterrows()}

    total_open = total_cents(open_counts)
    total_close = total_cents(close_counts)
    TARGET = int(st.session_state.target_dollars) * 100
    diff = total_close - TARGET

    a, b, c = st.columns(3)
    a.info("TOTAL OPEN: " + cents_to_str(total_open))
    b.success("TOTAL CLOSE: " + cents_to_str(total_close))
    c.write("**À retirer:** " + f"**{cents_to_str(diff)}**")

    st.divider()
    st.subheader("Retrait proposé (favorise gros billets) + ajustement ➖/➕")

    retrait = {k: 0 for k in DENOMS}
    restant = dict(close_counts)

    if diff <= 0:
        st.warning("Sous la cible (ou égal).")
    else:
        coins_desc = sorted(COINS, key=lambda x: DENOMS[x], reverse=True)
        rolls_desc = sorted(ROLLS, key=lambda x: DENOMS[x], reverse=True)
        priority_caisse = BILLS_BIG + BILLS_SMALL + coins_desc + rolls_desc

        st.session_state.locked_retrait_caisse = clamp_locked(st.session_state.locked_retrait_caisse, close_counts)
        retrait, remaining = suggest_withdrawal(
            diff,
            close_counts,
            dict(st.session_state.locked_retrait_caisse),
            priority_caisse,
        )

        if remaining == 0:
            st.success("Retrait proposé: " + cents_to_str(total_cents(retrait)))
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining))

        if st.button("Reset ajustements retrait", key="btn_reset_caisse_locks"):
            st.session_state.locked_retrait_caisse = {}
            st.rerun()

        # +/- grid
        keys = DISPLAY_ORDER
        for i in range(0, len(keys), 4):
            row = st.columns(4)
            for j, k in enumerate(keys[i:i+4]):
                with row[j]:
                    q = safe_int(retrait.get(k, 0))
                    mx = safe_int(close_counts.get(k, 0))
                    st.markdown(f"**{k}**")
                    mcol, pcol = st.columns(2)
                    minus = mcol.button("➖", key=f"c_minus_{k}")
                    plus = pcol.button("➕", key=f"c_plus_{k}")
                    st.caption(f"Retrait: {q} | Dispo: {mx}")

                    if minus or plus:
                        new_locked = dict(st.session_state.locked_retrait_caisse)
                        if k not in new_locked:
                            new_locked[k] = q
                        if minus:
                            new_locked[k] = max(0, safe_int(new_locked[k]) - 1)
                        if plus:
                            new_locked[k] = min(mx, safe_int(new_locked[k]) + 1)
                        st.session_state.locked_retrait_caisse = new_locked
                        st.rerun()

        restant = sub_counts(close_counts, retrait)
        st.info("RESTANT: " + cents_to_str(total_cents(restant)))

    # Build rows for receipt
    rows_caisse = []
    for k in DISPLAY_ORDER:
        rows_caisse.append({
            "Dénomination": k,
            "OPEN": safe_int(open_counts.get(k, 0)),
            "CLOSE": safe_int(close_counts.get(k, 0)),
            "RETRAIT": safe_int(retrait.get(k, 0)),
            "RESTANT": safe_int(restant.get(k, 0)),
        })
    rows_caisse.append({
        "Dénomination": "TOTAL ($)",
        "OPEN": f"{total_open/100:.2f}",
        "CLOSE": f"{total_close/100:.2f}",
        "RETRAIT": f"{total_cents(retrait)/100:.2f}",
        "RESTANT": f"{total_cents(restant)/100:.2f}",
    })

    meta_caisse = {
        "Type": "CAISSE",
        "Date": today.isoformat(),
        "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "Caisse #": int(st.session_state.register_no),
        "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
        "Cible $": int(st.session_state.target_dollars),
    }

    payload_caisse = {
        "meta": meta_caisse,
        "register": {
            "table": st.session_state.df_register.to_dict("records"),
            "locked_retrait": st.session_state.locked_retrait_caisse,
            "rows": rows_caisse,
        },
    }

    # Autosave caisse
    hc = hash_payload(payload_caisse)
    if st.session_state.last_hash_caisse != hc:
        html = receipt_html(
            "Reçu — Caisse",
            meta_caisse,
            ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"],
            rows_caisse,
        )
        save_state(DIR_CAISSE, today, payload_caisse)
        save_receipt(DIR_CAISSE, today, html)
        st.session_state.last_hash_caisse = hc

    st.markdown("### Aperçu reçu — Caisse")
    components.html(
        receipt_html("Reçu — Caisse", meta_caisse, ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"], rows_caisse),
        height=620,
        scrolling=True,
    )

# ================== TAB: BOÎTE (ÉCHANGE) ==================
with tab_boite:
    st.subheader("Boîte (Échange) — Dépôt billets → Retrait change")
    st.caption("Tu saisis l’inventaire de la boîte (avant), puis le dépôt. L’app retire du change pour la même valeur. Priorité: 20/10/5 puis pièces.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Boîte (avant) — Inventaire")
        edited_before = st.data_editor(
            st.session_state.df_box_before,
            hide_index=True,
            use_container_width=True,
            height=420,
            column_config={
                "Dénomination": st.column_config.TextColumn(disabled=True),
                "Boîte (avant)": st.column_config.NumberColumn(min_value=0, step=1),
            },
            key="ed_box_before",
        )
        st.session_state.df_box_before = edited_before

    with right:
        st.markdown("#### Dépôt (ce que tu mets dans la boîte)")
        edited_deposit = st.data_editor(
            st.session_state.df_box_deposit,
            hide_index=True,
            use_container_width=True,
            height=420,
            column_config={
                "Dénomination": st.column_config.TextColumn(disabled=True),
                "Dépôt": st.column_config.NumberColumn(min_value=0, step=1),
            },
            key="ed_box_deposit",
        )
        st.session_state.df_box_deposit = edited_deposit

    box_before = {r["Dénomination"]: safe_int(r["Boîte (avant)"]) for _, r in st.session_state.df_box_before.iterrows()}
    deposit = {r["Dénomination"]: safe_int(r["Dépôt"]) for _, r in st.session_state.df_box_deposit.iterrows()}

    total_before = total_cents(box_before)
    total_deposit = total_cents(deposit)

    s1, s2, s3 = st.columns(3)
    s1.info("Boîte (avant): " + cents_to_str(total_before))
    s2.success("Dépôt total: " + cents_to_str(total_deposit))
    s3.write("**Change à retirer (objectif):** " + f"**{cents_to_str(total_deposit)}**")

    st.divider()
    st.subheader("Change retiré proposé + ajustements ➖/➕")

    box_after_deposit = add_counts(box_before, deposit)

    # Priority per your spec: small bills + small coins first
    priority_boite = (
        ["Billet 20 $", "Billet 10 $", "Billet 5 $"] +
        ["Pièce 2 $", "Pièce 1 $", "Pièce 0,25 $", "Pièce 0,10 $", "Pièce 0,05 $"] +
        ROLLS +
        ["Billet 50 $", "Billet 100 $"]
    )

    withdraw = {k: 0 for k in DENOMS}
    remaining = 0

    st.session_state.locked_withdraw_boite = clamp_locked(st.session_state.locked_withdraw_boite, box_after_deposit)

    if total_deposit == 0:
        st.warning("Dépôt = 0. Rien à calculer.")
    else:
        withdraw, remaining = suggest_withdrawal(
            total_deposit,
            box_after_deposit,
            dict(st.session_state.locked_withdraw_boite),
            priority_boite,
        )

        if remaining == 0:
            st.success("Change retiré: " + cents_to_str(total_cents(withdraw)))
        elif remaining < 0:
            st.warning("Verrouillage trop haut. Dépasse de " + cents_to_str(-remaining))
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining))

    if st.button("Reset ajustements (boîte)", key="btn_reset_boite_locks"):
        st.session_state.locked_withdraw_boite = {}
        st.rerun()

    # +/- grid
    keys = DISPLAY_ORDER
    for i in range(0, len(keys), 4):
        row = st.columns(4)
        for j, k in enumerate(keys[i:i+4]):
            with row[j]:
                q = safe_int(withdraw.get(k, 0))
                mx = safe_int(box_after_deposit.get(k, 0))
                st.markdown(f"**{k}**")
                mcol, pcol = st.columns(2)
                minus = mcol.button("➖", key=f"b_minus_{k}")
                plus = pcol.button("➕", key=f"b_plus_{k}")
                st.caption(f"Retrait: {q} | Dispo: {mx}")
                if minus or plus:
                    new_locked = dict(st.session_state.locked_withdraw_boite)
                    if k not in new_locked:
                        new_locked[k] = q
                    if minus:
                        new_locked[k] = max(0, safe_int(new_locked[k]) - 1)
                    if plus:
                        new_locked[k] = min(mx, safe_int(new_locked[k]) + 1)
                    st.session_state.locked_withdraw_boite = new_locked
                    st.rerun()

    box_after = sub_counts(box_after_deposit, withdraw)
    st.info("Boîte (après échange): " + cents_to_str(total_cents(box_after)))

    rows_boite = []
    for k in DISPLAY_ORDER:
        rows_boite.append({
            "Dénomination": k,
            "Boîte (avant)": safe_int(box_before.get(k, 0)),
            "Dépôt": safe_int(deposit.get(k, 0)),
            "Change retiré": safe_int(withdraw.get(k, 0)),
            "Boîte (après)": safe_int(box_after.get(k, 0)),
        })
    rows_boite.append({
        "Dénomination": "TOTAL ($)",
        "Boîte (avant)": f"{total_before/100:.2f}",
        "Dépôt": f"{total_deposit/100:.2f}",
        "Change retiré": f"{total_cents(withdraw)/100:.2f}",
        "Boîte (après)": f"{total_cents(box_after)/100:.2f}",
    })

    meta_boite = {
        "Type": "BOÎTE (ÉCHANGE)",
        "Date": today.isoformat(),
        "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
        "Caisse #": int(st.session_state.register_no),
        "Dépôt total ($)": f"{total_deposit/100:.2f}",
    }

    payload_boite = {
        "meta": meta_boite,
        "boite": {
            "table_before": st.session_state.df_box_before.to_dict("records"),
            "table_deposit": st.session_state.df_box_deposit.to_dict("records"),
            "locked_withdraw": st.session_state.locked_withdraw_boite,
            "rows": rows_boite,
        },
    }

    # Autosave boite
    hb = hash_payload(payload_boite)
    if st.session_state.last_hash_boite != hb:
        html = receipt_html(
            "Reçu — Boîte (Échange)",
            meta_boite,
            ["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"],
            rows_boite,
        )
        save_state(DIR_BOITE, today, payload_boite)
        save_receipt(DIR_BOITE, today, html)
        st.session_state.last_hash_boite = hb

    st.markdown("### Aperçu reçu — Boîte (Échange)")
    components.html(
        receipt_html("Reçu — Boîte (Échange)", meta_boite, ["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"], rows_boite),
        height=620,
        scrolling=True,
    )

# ================== TAB: SAUVEGARDE & REÇUS ==================
with tab_save:
    st.subheader("Sauvegarde & reçus")
    st.caption("Liste simple. Clique pour ouvrir. Téléchargements séparés (Caisse vs Boîte).")

    colA, colB = st.columns(2)

    with colA:
        st.markdown("## 📒 Caisse")
        dates = list_dates(DIR_CAISSE)
        if not dates:
            st.info("Aucun enregistrement Caisse.")
        else:
            for ds in reversed(dates):
                d = date.fromisoformat(ds)
                with st.expander(f"{ds} — Reçu Caisse", expanded=False):
                    html, _ = load_receipt(DIR_CAISSE, d)
                    rp = os.path.join(DIR_CAISSE, f"{ds}_receipt.html")
                    sp = os.path.join(DIR_CAISSE, f"{ds}_state.json")

                    if html:
                        components.html(html, height=640, scrolling=True)
                    else:
                        st.warning("Reçu introuvable pour cette date.")

                    # Unique keys to avoid StreamlitDuplicateElementId
                    if os.path.exists(rp):
                        with open(rp, "rb") as f:
                            st.download_button(
                                "⬇️ Télécharger reçu (HTML)",
                                data=f.read(),
                                file_name=os.path.basename(rp),
                                mime="text/html",
                                key=f"dl_caisse_html_{ds}",
                            )
                    if os.path.exists(sp):
                        with open(sp, "rb") as f:
                            st.download_button(
                                "⬇️ Télécharger état (JSON)",
                                data=f.read(),
                                file_name=os.path.basename(sp),
                                mime="application/json",
                                key=f"dl_caisse_json_{ds}",
                            )

    with colB:
        st.markdown("## 🪙 Boîte (Échange)")
        dates = list_dates(DIR_BOITE)
        if not dates:
            st.info("Aucun enregistrement Boîte.")
        else:
            for ds in reversed(dates):
                d = date.fromisoformat(ds)
                with st.expander(f"{ds} — Reçu Boîte (Échange)", expanded=False):
                    html, _ = load_receipt(DIR_BOITE, d)
                    rp = os.path.join(DIR_BOITE, f"{ds}_receipt.html")
                    sp = os.path.join(DIR_BOITE, f"{ds}_state.json")

                    if html:
                        components.html(html, height=640, scrolling=True)
                    else:
                        st.warning("Reçu introuvable pour cette date.")

                    if os.path.exists(rp):
                        with open(rp, "rb") as f:
                            st.download_button(
                                "⬇️ Télécharger reçu (HTML)",
                                data=f.read(),
                                file_name=os.path.basename(rp),
                                mime="text/html",
                                key=f"dl_boite_html_{ds}",
                            )
                    if os.path.exists(sp):
                        with open(sp, "rb") as f:
                            st.download_button(
                                "⬇️ Télécharger état (JSON)",
                                data=f.read(),
                                file_name=os.path.basename(sp),
                                mime="application/json",
                                key=f"dl_boite_json_{ds}",
                            )
