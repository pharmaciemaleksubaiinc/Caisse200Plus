# caisse200_plus.py
# Registre quotidien — Caisse (retour cible) + Boîte de monnaie + Historique
# Fixes:
# - NO reset while typing: use st.form() for counting
# - Printable "receipt" HTML + Print button
# - 3rd tab "Historique" to reopen any day
# - Plus/minus buttons for quick adjustments
# - Autosave per day (JSON + HTML receipt). Excel optional if openpyxl installed.

import os
import json
import hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

TZ = ZoneInfo("America/Toronto")

# ------------------ PATHS ------------------
DATA_DIR = "data"
RECORDS_DIR = os.path.join(DATA_DIR, "records")
os.makedirs(RECORDS_DIR, exist_ok=True)

def day_key(d: date) -> str:
    return d.isoformat()

def state_path(d: date) -> str:
    return os.path.join(RECORDS_DIR, f"{day_key(d)}_state.json")

def receipt_path(d: date) -> str:
    return os.path.join(RECORDS_DIR, f"{day_key(d)}_receipt.html")

# ------------------ CONFIG ------------------
st.set_page_config(page_title="Caisse & Boîte — Registre", layout="wide")

# ------------------ AUTH ------------------
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

# ------------------ DENOMS ------------------
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

# ------------------ HELPERS ------------------
def cents_to_str(c: int) -> str:
    return f"{c/100:.2f} $"

def total_cents(counts: dict) -> int:
    return sum(int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)

def sub_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) - int(b.get(k, 0)) for k in DENOMS}

def add_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in DENOMS}

def clamp_locked(locked: dict, avail: dict) -> dict:
    out = {}
    for k, v in locked.items():
        v = int(v)
        if v < 0:
            v = 0
        mx = int(avail.get(k, 0))
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
        can_take = int(avail.get(k, 0)) - int(out.get(k, 0))
        if can_take < 0:
            can_take = 0
        take = min(remaining // v, can_take)
        if take > 0:
            out[k] = int(out.get(k, 0)) + int(take)
            remaining -= int(take) * v
    return remaining

def suggest(amount_cents: int, allowed: list, avail: dict, locked: dict, priority: list):
    out = {k: 0 for k in DENOMS}
    for k, q in locked.items():
        out[k] = int(q)

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

# ------------------ STORAGE ------------------
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
    reg = payload.get("register", {})
    box = payload.get("changebox", {})

    def table_html(title, headers, rows):
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

    meta_lines = "".join([f"<div class='meta-row'><b>{k}:</b> {v}</div>" for k, v in meta.items()])

    reg_rows = reg.get("rows_calc", [])
    box_rows = box.get("rows_calc", [])

    html = f"""
    <html>
    <head>
      <meta charset="utf-8" />
      <title>Reçu — Registre</title>
      <style>
        body {{ font-family: Arial, sans-serif; padding: 18px; color:#111; }}
        .top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }}
        .meta {{ font-size: 13px; opacity: 0.9; }}
        .section {{ margin-top: 18px; }}
        h2 {{ margin:0; }}
        h3 {{ margin: 0 0 8px 0; }}
        table {{ width:100%; border-collapse: collapse; font-size: 13px; }}
        th, td {{ border: 1px solid #222; padding: 6px; }}
        th {{ background: #f0f0f0; }}
        .btnbar {{ margin-top: 12px; display:flex; gap:10px; }}
        button {{ padding:10px 14px; border-radius:10px; border:1px solid #bbb; background:#fff; cursor:pointer; font-weight:600; }}
        .note {{ font-size:12px; opacity:0.7; margin-top:6px; }}
      </style>
    </head>
    <body>
      <div class="top">
        <div>
          <h2>Registre — Caisse & Boîte de monnaie</h2>
          <div class="note">Impression: utilise le bouton ci-dessous.</div>
        </div>
        <div class="meta">{meta_lines}</div>
      </div>

      <div class="btnbar">
        <button onclick="window.print()">🖨️ Imprimer</button>
      </div>

      {table_html("Caisse — Calcul", ["Dénomination","OPEN","CLOSE","RETRAIT","RESTANT"], reg_rows)}
      {table_html("Boîte de monnaie — Calcul", ["Dénomination","Boîte (actuel)","Vers boîte","Depuis boîte","Boîte (après)"], box_rows)}
    </body>
    </html>
    """
    return html

def save_receipt_file(d: date, payload: dict):
    html = build_receipt_html(payload)
    with open(receipt_path(d), "w", encoding="utf-8") as f:
        f.write(html)

# ------------------ STATE BOOT ------------------
today = datetime.now(TZ).date()

if "active_date" not in st.session_state:
    st.session_state.active_date = today

if "booted_for" not in st.session_state or st.session_state.booted_for != today:
    st.session_state.booted_for = today
    st.session_state.active_date = today

    existing = load_day(today)

    # meta
    st.session_state.cashier = (existing or {}).get("meta", {}).get("Caissier(ère)", "")
    st.session_state.register_no = int((existing or {}).get("meta", {}).get("Caisse #", 1))
    st.session_state.target_dollars = int((existing or {}).get("meta", {}).get("Cible $", 200))
    st.session_state.box_target_dollars = int((existing or {}).get("changebox", {}).get("box_target_dollars", 0))

    # counts
    st.session_state.open_counts = (existing or {}).get("register", {}).get("open_counts", {k: 0 for k in DENOMS})
    st.session_state.close_counts = (existing or {}).get("register", {}).get("close_counts", {k: 0 for k in DENOMS})
    st.session_state.allowed_retrait = (existing or {}).get("register", {}).get("allowed_retrait", list(DISPLAY_ORDER))

    st.session_state.box_now = (existing or {}).get("changebox", {}).get("box_now", {k: 0 for k in DENOMS})
    st.session_state.allowed_box = (existing or {}).get("changebox", {}).get("allowed_box", list(DISPLAY_ORDER))

    # locks
    st.session_state.locked_retrait = (existing or {}).get("register", {}).get("locked_retrait", {})
    st.session_state.locked_to_box = (existing or {}).get("changebox", {}).get("locked_to_box", {})
    st.session_state.locked_from_box = (existing or {}).get("changebox", {}).get("locked_from_box", {})

    st.session_state.last_saved_hash = None
    st.session_state.last_saved_at = None

# ------------------ UI HEADER ------------------
st.title("Registre quotidien — Caisse & Boîte de monnaie")
top = st.columns([1.2, 1.2, 1.5, 1.8, 2.3])

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

# ------------------ INPUT GRID W/ +/- ------------------
def denom_grid(title: str, prefix: str, counts: dict):
    st.subheader(title)
    st.caption("Astuce: utilise ➖/➕ pour ajuster vite. Rien ne se reset pendant que tu tapes (form).")

    # 4 columns grid
    cols_per_row = 4
    keys = DISPLAY_ORDER

    for i in range(0, len(keys), cols_per_row):
        row_cols = st.columns(cols_per_row)
        for j, k in enumerate(keys[i:i+cols_per_row]):
            with row_cols[j]:
                st.markdown(f"**{k}**")
                m, p = st.columns([1,1])
                if m.button("➖", key=f"{prefix}_minus_{k}"):
                    counts[k] = max(0, int(counts.get(k, 0)) - 1)
                if p.button("➕", key=f"{prefix}_plus_{k}"):
                    counts[k] = int(counts.get(k, 0)) + 1

                counts[k] = st.number_input(
                    "Qté",
                    min_value=0,
                    step=1,
                    value=int(counts.get(k, 0)),
                    key=f"{prefix}_qty_{k}",
                    label_visibility="collapsed",
                )

# ------------------ CALC ROWS ------------------
def rows_caisse(open_c, close_c, retrait_c, restant_c):
    rows = []
    for k in DISPLAY_ORDER:
        rows.append({
            "Dénomination": k,
            "OPEN": int(open_c.get(k, 0)),
            "CLOSE": int(close_c.get(k, 0)),
            "RETRAIT": int(retrait_c.get(k, 0)),
            "RESTANT": int(restant_c.get(k, 0)),
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
            "Boîte (actuel)": int(box_now.get(k, 0)),
            "Vers boîte": int(to_box.get(k, 0)),
            "Depuis boîte": int(from_box.get(k, 0)),
            "Boîte (après)": int(box_after.get(k, 0)),
        })
    rows.append({
        "Dénomination": "TOTAL ($)",
        "Boîte (actuel)": f"{total_cents(box_now)/100:.2f}",
        "Vers boîte": f"{total_cents(to_box)/100:.2f}",
        "Depuis boîte": f"{total_cents(from_box)/100:.2f}",
        "Boîte (après)": f"{total_cents(box_after)/100:.2f}",
    })
    return rows

# ------------------ TABS ------------------
tab1, tab2, tab3 = st.tabs(["1) Caisse", "2) Boîte de monnaie", "3) Historique / Reçus"])

# ========================= TAB 1 =========================
with tab1:
    with st.form("form_caisse", clear_on_submit=False):
        denom_grid("OPEN — Fond de caisse", "open", st.session_state.open_counts)
        st.info("TOTAL OPEN: " + cents_to_str(total_cents(st.session_state.open_counts)))

        st.divider()

        denom_grid("CLOSE — Comptage fin de journée", "close", st.session_state.close_counts)
        st.success("TOTAL CLOSE: " + cents_to_str(total_cents(st.session_state.close_counts)))

        st.divider()

        st.subheader("Types autorisés (retrait)")
        a1, a2, a3 = st.columns(3)
        allowed = []
        for idx, k in enumerate(DISPLAY_ORDER):
            col = [a1, a2, a3][idx % 3]
            with col:
                if st.checkbox(k, value=(k in st.session_state.allowed_retrait), key=f"allow_retrait_{k}"):
                    allowed.append(k)

        submitted = st.form_submit_button("✅ Enregistrer / Calculer (Caisse)")

        if submitted:
            st.session_state.allowed_retrait = allowed

    # Compute retrait after form submit (or from current state)
    TARGET = int(st.session_state.target_dollars) * 100
    total_close = total_cents(st.session_state.close_counts)
    diff = total_close - TARGET

    st.divider()
    st.subheader("RETRAIT — Proposition + ajustements")

    st.write("Cible:", f"**{cents_to_str(TARGET)}**")
    st.write("À retirer (CLOSE - cible):", f"**{cents_to_str(diff)}**")

    retrait_counts = {k: 0 for k in DENOMS}
    restant_counts = dict(st.session_state.close_counts)
    remaining = 0

    if diff <= 0:
        st.warning("Sous la cible (ou égal). Ici il faudrait AJOUTER, pas retirer.")
    elif not st.session_state.allowed_retrait:
        st.error("Choisis au moins un type autorisé.")
    else:
        st.session_state.locked_retrait = clamp_locked(st.session_state.locked_retrait, st.session_state.close_counts)
        locked = dict(st.session_state.locked_retrait)

        retrait_counts, remaining = suggest_retrait_caisse(diff, st.session_state.allowed_retrait, st.session_state.close_counts, locked)

        if remaining == 0:
            st.success("Retrait proposé: " + cents_to_str(total_cents(retrait_counts)))
        elif remaining < 0:
            st.warning("Retrait dépasse de " + cents_to_str(-remaining) + " (verrouillage trop haut).")
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining))

        # Adjustment grid
        adjust_keys = [k for k in DISPLAY_ORDER if k in st.session_state.allowed_retrait]
        cols_per_row = 4

        for i in range(0, len(adjust_keys), cols_per_row):
            row_cols = st.columns(cols_per_row)
            for j, k in enumerate(adjust_keys[i:i+cols_per_row]):
                with row_cols[j]:
                    q = int(retrait_counts.get(k, 0))
                    mx = int(st.session_state.close_counts.get(k, 0))

                    st.markdown(f"**{k}**")
                    m, p = st.columns(2)
                    minus = m.button("➖", key=f"r_minus_{k}")
                    plus = p.button("➕", key=f"r_plus_{k}")
                    st.write(f"Retrait: **{q}**")
                    st.caption(f"Dispo: {mx}")

                    if minus or plus:
                        new_locked = dict(st.session_state.locked_retrait)
                        if k not in new_locked:
                            new_locked[k] = q
                        if minus:
                            new_locked[k] = max(0, int(new_locked[k]) - 1)
                        if plus:
                            new_locked[k] = min(mx, int(new_locked[k]) + 1)
                        st.session_state.locked_retrait = new_locked
                        st.rerun()

        restant_counts = sub_counts(st.session_state.close_counts, retrait_counts)
        st.info("RESTANT (après retrait): " + cents_to_str(total_cents(restant_counts)))

    # Save restant_counts for tab2 linkage
    st.session_state._register_restant_counts = restant_counts
    st.session_state._register_retrait_counts = retrait_counts

# ========================= TAB 2 =========================
with tab2:
    st.session_state.box_target_dollars = st.number_input(
        "Cible boîte ($) (optionnel)",
        min_value=0,
        step=10,
        value=int(st.session_state.box_target_dollars),
    )

    with st.form("form_box", clear_on_submit=False):
        denom_grid("Boîte — état actuel", "box", st.session_state.box_now)
        st.info("TOTAL boîte (actuel): " + cents_to_str(total_cents(st.session_state.box_now)))

        st.divider()
        st.subheader("Types autorisés (boîte)")
        b1, b2, b3 = st.columns(3)
        allowed_box = []
        for idx, k in enumerate(DISPLAY_ORDER):
            col = [b1, b2, b3][idx % 3]
            with col:
                if st.checkbox(k, value=(k in st.session_state.allowed_box), key=f"allow_box_{k}"):
                    allowed_box.append(k)

        submitted_box = st.form_submit_button("✅ Enregistrer / Calculer (Boîte)")

        if submitted_box:
            st.session_state.allowed_box = allowed_box

    st.divider()
    st.subheader("Mouvements boîte (connecté à la caisse)")

    register_avail = st.session_state.get("_register_restant_counts", {k: 0 for k in DENOMS})
    st.write("Dispo depuis la caisse (RESTANT): **" + cents_to_str(total_cents(register_avail)) + "**")

    box_target = int(st.session_state.box_target_dollars) * 100
    box_total = total_cents(st.session_state.box_now)
    delta = box_target - box_total  # + => need add to box, - => remove from box

    to_box = {k: 0 for k in DENOMS}
    from_box = {k: 0 for k in DENOMS}

    if not st.session_state.allowed_box:
        st.error("Choisis au moins un type autorisé pour la boîte.")
    else:
        if delta > 0:
            st.write("**Action:** Ajouter à la boîte depuis la caisse:", f"**{cents_to_str(delta)}**")
            st.session_state.locked_to_box = clamp_locked(st.session_state.locked_to_box, register_avail)
            to_box, rem = suggest_changebox(delta, st.session_state.allowed_box, register_avail, dict(st.session_state.locked_to_box))

            if rem == 0:
                st.success("Vers boîte: " + cents_to_str(total_cents(to_box)))
            else:
                st.warning("Reste non couvert: " + cents_to_str(rem))

        elif delta < 0:
            need = -delta
            st.write("**Action:** Retirer depuis la boîte:", f"**{cents_to_str(need)}**")
            st.session_state.locked_from_box = clamp_locked(st.session_state.locked_from_box, st.session_state.box_now)
            from_box, rem = suggest_changebox(need, st.session_state.allowed_box, st.session_state.box_now, dict(st.session_state.locked_from_box))

            if rem == 0:
                st.success("Depuis boîte: " + cents_to_str(total_cents(from_box)))
            else:
                st.warning("Reste non couvert: " + cents_to_str(rem))
        else:
            st.success("Boîte déjà exactement à la cible.")

    box_after = add_counts(sub_counts(st.session_state.box_now, from_box), to_box)
    st.info("TOTAL boîte (après): " + cents_to_str(total_cents(box_after)))

    st.session_state._box_to = to_box
    st.session_state._box_from = from_box
    st.session_state._box_after = box_after

# ========================= SAVE / RECEIPT (GLOBAL) =========================
st.divider()
st.subheader("Sauvegarde & impression (reçu)")

meta = {
    "Date": today.isoformat(),
    "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    "Caisse #": int(st.session_state.register_no),
    "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
    "Cible $": int(st.session_state.target_dollars),
    "Cible boîte $": int(st.session_state.box_target_dollars),
}

# Build calc rows
open_c = st.session_state.open_counts
close_c = st.session_state.close_counts
retrait_c = st.session_state.get("_register_retrait_counts", {k: 0 for k in DENOMS})
restant_c = st.session_state.get("_register_restant_counts", dict(close_c))

rows_reg = rows_caisse(open_c, close_c, retrait_c, restant_c)

box_now = st.session_state.box_now
to_box = st.session_state.get("_box_to", {k: 0 for k in DENOMS})
from_box = st.session_state.get("_box_from", {k: 0 for k in DENOMS})
box_after = st.session_state.get("_box_after", box_now)

rows_b = rows_box(box_now, to_box, from_box, box_after)

payload = {
    "meta": meta,
    "register": {
        "open_counts": open_c,
        "close_counts": close_c,
        "allowed_retrait": st.session_state.allowed_retrait,
        "locked_retrait": st.session_state.locked_retrait,
        "rows_calc": rows_reg,
    },
    "changebox": {
        "box_now": box_now,
        "allowed_box": st.session_state.allowed_box,
        "box_target_dollars": int(st.session_state.box_target_dollars),
        "locked_to_box": st.session_state.locked_to_box,
        "locked_from_box": st.session_state.locked_from_box,
        "rows_calc": rows_b,
    },
}

def autosave(payload: dict):
    h = hash_payload(payload)
    if st.session_state.last_saved_hash == h:
        return
    save_day(today, payload)
    save_receipt_file(today, payload)
    st.session_state.last_saved_hash = h
    st.session_state.last_saved_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

autosave(payload)

c1, c2, c3, c4 = st.columns([1.3, 1.7, 2.0, 3.0])
with c1:
    if st.button("💾 Enregistrer maintenant"):
        save_day(today, payload)
        save_receipt_file(today, payload)
        st.session_state.last_saved_hash = hash_payload(payload)
        st.session_state.last_saved_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
with c2:
    st.write("Dernière sauvegarde:", st.session_state.last_saved_at or "—")
with c3:
    st.write("Fichiers:", f"`{os.path.basename(state_path(today))}` + `{os.path.basename(receipt_path(today))}`")
with c4:
    st.caption("Le reçu HTML est imprimable et garde l’historique. Les desktops n’installent rien, c’est le serveur qui compte.")

# Printable receipt preview + print button (embedded)
st.markdown("### Reçu imprimable (aperçu)")
receipt_html = build_receipt_html(payload)
components.html(receipt_html, height=700, scrolling=True)

# ========================= TAB 3: HISTORY =========================
with tab3:
    st.subheader("Historique / Reçus")
    files = sorted([f for f in os.listdir(RECORDS_DIR) if f.endswith("_state.json")])

    if not files:
        st.info("Aucun enregistrement trouvé encore.")
    else:
        dates = [f.replace("_state.json", "") for f in files]
        pick = st.selectbox("Choisir une date", dates, index=len(dates)-1)

        picked_date = date.fromisoformat(pick)
        loaded = load_day(picked_date)

        if not loaded:
            st.error("Impossible de charger l’enregistrement.")
        else:
            st.write("**Meta**")
            st.json(loaded.get("meta", {}))

            st.markdown("### Reçu imprimable (historique)")
            html_p = receipt_path(picked_date)

            if os.path.exists(html_p):
                with open(html_p, "r", encoding="utf-8") as f:
                    html_doc = f.read()
                components.html(html_doc, height=700, scrolling=True)

                # Download
                with open(html_p, "rb") as f:
                    st.download_button(
                        "⬇️ Télécharger le reçu HTML",
                        data=f,
                        file_name=os.path.basename(html_p),
                        mime="text/html",
                    )
            else:
                st.warning("Reçu HTML manquant pour cette date (il sera recréé au prochain save).")
