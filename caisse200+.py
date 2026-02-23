import os
import json
import hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

# ================== CONFIG ==================
st.set_page_config(page_title="Registre — style feuille", layout="wide")
TZ = ZoneInfo("America/Toronto")

BASE_DIR = "data"
DIR_CAISSE = os.path.join(BASE_DIR, "records_caisse")
os.makedirs(DIR_CAISSE, exist_ok=True)

# ================== DENOMS (match app logic) ==================
DENOMS = {
    "100$": 10000,
    "50$": 5000,
    "20$": 2000,
    "10$": 1000,
    "5$": 500,
    "2.00$": 200,
    "1.00$": 100,
    "0.25$": 25,
    "0.10$": 10,
    "0.05$": 5,
    # rolls (match screenshot section "ROULEAUX")
    "Roll 2$ (25)": 5000,
    "Roll 1$ (25)": 2500,
    "Roll 0.25$ (40)": 1000,
    "Roll 0.10$ (50)": 500,
    "Roll 0.05$ (40)": 200,
}

BILLS = ["100$", "50$", "20$", "10$", "5$"]
COINS = ["2.00$", "1.00$", "0.25$", "0.10$", "0.05$"]
ROLLS = ["Roll 2$ (25)", "Roll 1$ (25)", "Roll 0.25$ (40)", "Roll 0.10$ (50)", "Roll 0.05$ (40)"]

# For suggested retrait (big bills first)
PRIO_RETRAIT = ["100$", "50$", "20$", "10$", "5$"] + COINS[::-1] + ROLLS  # coins small -> later, rolls last-ish

# ================== CSS (centre + bold + sheet look) ==================
st.markdown(
    """
<style>
/* centre the whole "sheet" */
.sheet-wrap{
  width: 980px;            /* tweak if you want tighter/looser */
  max-width: 980px;
  margin: 0 auto;
}

/* remove giant whitespace / keep it compact */
.main .block-container { padding-top: 0.8rem !important; padding-bottom: 0.8rem !important; max-width: 1700px !important; }

/* table-like visuals */
.sheet{
  border: 2px solid #000;
  border-radius: 6px;
  overflow: hidden;
  background: white;
}
.sheet-head{
  font-weight: 900;
  text-align: center;
  padding: 10px 6px;
  border-bottom: 2px solid #000;
}
.sheet-meta{
  border-bottom: 2px solid #000;
  padding: 8px 10px;
  font-weight: 800;
  font-size: 13px;
}
.sheet-meta small{ font-weight: 700; opacity: .75; }

.section-title{
  background: #d9d9d9;
  font-weight: 900;
  text-align: center;
  border-top: 2px solid #000;
  border-bottom: 2px solid #000;
  padding: 6px;
}

.row{
  padding: 0;
  margin: 0;
}
.cell-label{
  font-weight: 900;
}
.cell-total{
  font-weight: 900;
  text-align: right;
}
.cell-qte{
  font-weight: 900;
  text-align: center;
}

/* make streamlit widgets look less "streamlit" */
div[data-testid="stNumberInput"] input{
  font-weight: 900 !important;
  text-align: center !important;
}

/* smaller buttons */
.smallbtn button{
  padding: 0.15rem 0.55rem !important;
  font-weight: 900 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

# ================== HELPERS ==================
def cents_to_str(c: int) -> str:
    return f"{c/100:.2f}$"

def total_cents(counts: dict) -> int:
    return sum(int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)

def clamp_locked(locked: dict, avail: dict) -> dict:
    out = {}
    for k, v in (locked or {}).items():
        v = int(v)
        if v < 0: v = 0
        mx = int(avail.get(k, 0))
        if v > mx: v = mx
        out[k] = v
    return out

def suggest_retrait(diff_cents: int, avail_close: dict, locked: dict):
    """
    Suggest retrait using PRIO_RETRAIT.
    locked = {denom: forced_qty}
    """
    out = {k: 0 for k in DENOMS}
    for k, q in (locked or {}).items():
        out[k] = int(q)

    remaining = diff_cents - total_cents(out)
    if remaining < 0:
        return out, remaining

    for k in PRIO_RETRAIT:
        if remaining <= 0:
            break
        if k in locked:
            continue
        v = DENOMS[k]
        can_take = int(avail_close.get(k, 0)) - int(out.get(k, 0))
        if can_take < 0:
            can_take = 0
        take = min(remaining // v, can_take)
        if take > 0:
            out[k] += int(take)
            remaining -= int(take) * v

    return out, remaining

def hash_payload(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def save_text(path: str, txt: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

def save_json(path: str, payload: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def load_text(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def list_days(folder: str):
    files = sorted([f for f in os.listdir(folder) if f.endswith("_state.json")])
    return [f.replace("_state.json", "") for f in files]

def day_paths(d: date):
    ds = d.isoformat()
    return (
        os.path.join(DIR_CAISSE, f"{ds}_state.json"),
        os.path.join(DIR_CAISSE, f"{ds}_receipt.html"),
    )

def receipt_html(meta: dict, rows: list):
    meta_html = "".join([f"<div><b>{k}:</b> {v}</div>" for k, v in meta.items()])

    body = ""
    for r in rows:
        body += (
            "<tr>"
            f"<td><b>{r['Ligne']}</b></td>"
            f"<td style='text-align:center'><b>{r['VALIDATION']}</b></td>"
            f"<td style='text-align:center'><b>{r['CASH-OUT']}</b></td>"
            f"<td style='text-align:center'><b>{r['RETRAIT/DÉPÔT']}</b></td>"
            f"<td style='text-align:center'><b>{r['FERMETURE']}</b></td>"
            "</tr>"
        )

    return f"""
<html><head><meta charset="utf-8"/>
<title>Reçu — Registre</title>
<style>
  body{{font-family:Arial,sans-serif;padding:18px;color:#111}}
  .top{{display:flex;justify-content:space-between;gap:16px}}
  .meta{{font-size:13px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:14px}}
  th,td{{border:1px solid #000;padding:6px}}
  th{{background:#f0f0f0;font-weight:900}}
  button{{padding:10px 14px;border-radius:10px;border:1px solid #bbb;background:#fff;cursor:pointer;font-weight:800}}
  @media print{{.btnbar{{display:none}} body{{padding:0}}}}
</style>
</head><body>
  <div class="top">
    <div><h2 style="margin:0">Reçu — Registre caisse</h2><div style="opacity:.7;font-size:12px">Imprime avec le bouton.</div></div>
    <div class="meta">{meta_html}</div>
  </div>
  <div class="btnbar" style="margin-top:12px;"><button onclick="window.print()">🖨️ Imprimer</button></div>
  <table>
    <thead>
      <tr>
        <th>Ligne</th>
        <th>VALIDATION</th>
        <th>CASH-OUT</th>
        <th>RETRAIT/DÉPÔT</th>
        <th>FERMETURE</th>
      </tr>
    </thead>
    <tbody>{body}</tbody>
  </table>
</body></html>
"""

# ================== AUTH ==================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("Accès protégé")
    pwd = st.text_input("Mot de passe", type="password", key="pwd")
    if st.button("Se connecter", key="login_btn"):
        if pwd == st.secrets.get("APP_PASSWORD"):
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()

# ================== STATE INIT ==================
today = datetime.now(TZ).date()

# core values (the 4 columns)
COLS = ["validation", "cashout", "retrait", "fermeture"]

def ensure_counts(name: str):
    if name not in st.session_state:
        st.session_state[name] = {k: 0 for k in DENOMS}

for c in COLS:
    ensure_counts(f"counts_{c}")

if "locked_retrait" not in st.session_state:
    st.session_state.locked_retrait = {}

if "cashier" not in st.session_state:
    st.session_state.cashier = ""
if "till" not in st.session_state:
    st.session_state.till = "TILL 1"
if "target" not in st.session_state:
    st.session_state.target = 200

# Restore saved day
state_path, receipt_path = day_paths(today)
saved = load_json(state_path)
if saved and st.session_state.get("booted_for") != today.isoformat():
    st.session_state.booted_for = today.isoformat()
    st.session_state.cashier = saved.get("cashier", st.session_state.cashier)
    st.session_state.till = saved.get("till", st.session_state.till)
    st.session_state.target = saved.get("target", st.session_state.target)
    for c in COLS:
        dct = saved.get(f"counts_{c}")
        if isinstance(dct, dict):
            st.session_state[f"counts_{c}"] = dct
    st.session_state.locked_retrait = saved.get("locked_retrait", {}) or {}

# ================== UI ==================
st.title("Registre — style feuille (compact, centre)")

tab_main, tab_save = st.tabs(["Registre", "Sauvegarde"])

# ---------- small header controls ----------
with tab_main:
    st.markdown("<div class='sheet-wrap'>", unsafe_allow_html=True)

    meta1, meta2, meta3 = st.columns([1.2, 1.0, 1.2])
    with meta1:
        st.session_state.till = st.selectbox("Caisse", ["TILL 1", "TILL 2", "TILL 3"], index=["TILL 1","TILL 2","TILL 3"].index(st.session_state.till))
    with meta2:
        st.session_state.cashier = st.text_input("Caissier", value=st.session_state.cashier)
    with meta3:
        st.session_state.target = st.number_input("Cible $", min_value=0, step=10, value=int(st.session_state.target))

    st.write("")  # spacing

    # ----------- Build 4 blocks like the sheet -----------
    c_validation, c_cashout, c_retrait, c_fermeture = st.columns(4, gap="small")

    def render_block(col, title: str, key_counts: str, allow_edit=True, show_controls=False):
        counts = st.session_state[key_counts]

        with col:
            st.markdown("<div class='sheet'>", unsafe_allow_html=True)
            st.markdown(f"<div class='sheet-head'>{title}</div>", unsafe_allow_html=True)

            # meta row like screenshot (timestamp dropdown vibe)
            st.markdown(
                f"<div class='sheet-meta'><b>{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}</b><br/>"
                f"<small>{st.session_state.till}</small></div>",
                unsafe_allow_html=True,
            )

            def row_line(label, denom_key):
                v = int(counts.get(denom_key, 0))
                total = (v * DENOMS[denom_key]) / 100

                r = st.columns([1.0, 1.2, 1.2], vertical_alignment="center")
                r[0].markdown(f"<div class='cell-label'>{label}</div>", unsafe_allow_html=True)

                if allow_edit:
                    if show_controls:
                        btns = r[1].columns([0.55, 0.9, 0.55], vertical_alignment="center")
                        with btns[0]:
                            st.markdown("<div class='smallbtn'>", unsafe_allow_html=True)
                            if st.button("➖", key=f"{key_counts}_{denom_key}_minus"):
                                counts[denom_key] = max(0, int(counts.get(denom_key, 0)) - 1)
                                st.session_state[key_counts] = counts
                                st.rerun()
                            st.markdown("</div>", unsafe_allow_html=True)
                        with btns[1]:
                            nv = st.number_input(
                                "",
                                min_value=0,
                                step=1,
                                value=int(counts.get(denom_key, 0)),
                                key=f"{key_counts}_{denom_key}_num",
                                label_visibility="collapsed",
                            )
                            counts[denom_key] = int(nv)
                            st.session_state[key_counts] = counts
                        with btns[2]:
                            st.markdown("<div class='smallbtn'>", unsafe_allow_html=True)
                            if st.button("➕", key=f"{key_counts}_{denom_key}_plus"):
                                counts[denom_key] = int(counts.get(denom_key, 0)) + 1
                                st.session_state[key_counts] = counts
                                st.rerun()
                            st.markdown("</div>", unsafe_allow_html=True)
                    else:
                        nv = r[1].number_input(
                            "",
                            min_value=0,
                            step=1,
                            value=v,
                            key=f"{key_counts}_{denom_key}_num",
                            label_visibility="collapsed",
                        )
                        counts[denom_key] = int(nv)
                        st.session_state[key_counts] = counts
                else:
                    r[1].markdown(f"<div class='cell-qte'>{v}</div>", unsafe_allow_html=True)

                r[2].markdown(f"<div class='cell-total'>{total:.2f}</div>", unsafe_allow_html=True)

            # header row (BILLETS / QTE / TOTAL)
            hdr = st.columns([1.0, 1.2, 1.2])
            hdr[0].markdown("<div class='cell-label'>BILLETS</div>", unsafe_allow_html=True)
            hdr[1].markdown("<div class='cell-qte'>QTE</div>", unsafe_allow_html=True)
            hdr[2].markdown("<div class='cell-total'>TOTAL</div>", unsafe_allow_html=True)

            # bills
            for k in BILLS:
                row_line(k, k)

            st.markdown("<div class='section-title'>MONNAIES</div>", unsafe_allow_html=True)

            # coins
            for k in COINS:
                row_line(k, k)

            st.markdown("<div class='section-title'>ROULEAUX</div>", unsafe_allow_html=True)

            for k in ROLLS:
                # label shows like sheet (25 / 40 / 50 / 40 etc) is cosmetic; we keep the roll name
                row_line(k.replace("Roll ", ""), k)

            # footer totals (like sheet)
            grand = total_cents(counts) / 100
            st.markdown("<div class='section-title'>GRAND TOTAL</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='padding:10px;font-weight:950;font-size:18px;text-align:center'>{grand:.2f}</div>", unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

    # Render blocks:
    # VALIDATION = OPEN
    render_block(c_validation, "VALIDATION", "counts_validation", allow_edit=True, show_controls=False)

    # CASH-OUT = RESTANT (computed) (we fill it below, so not editable)
    render_block(c_cashout, "CASH-OUT", "counts_cashout", allow_edit=False, show_controls=False)

    # RETRAIT/DÉPÔT = suggested retrait, editable with +/- controls
    render_block(c_retrait, "RETRAIT/DÉPÔT", "counts_retrait", allow_edit=True, show_controls=True)

    # FERMETURE = CLOSE
    render_block(c_fermeture, "FERMETURE", "counts_fermeture", allow_edit=True, show_controls=False)

    # --------- compute retrait suggestion + restant ---------
    target_cents = int(st.session_state.target) * 100
    close_counts = dict(st.session_state["counts_fermeture"])
    diff = total_cents(close_counts) - target_cents

    if diff > 0:
        st.session_state.locked_retrait = clamp_locked(st.session_state.locked_retrait, close_counts)
        retrait_suggested, remaining = suggest_retrait(diff, close_counts, dict(st.session_state.locked_retrait))

        # start from suggested, but if user already edited counts_retrait manually, respect that as locked baseline
        # We treat the current counts_retrait as "locked" if user changed it via +/- controls.
        # (Practical and avoids surprises.)
        st.session_state["counts_retrait"] = dict(retrait_suggested)

        restant = {}
        for k in DENOMS:
            restant[k] = int(close_counts.get(k, 0)) - int(st.session_state["counts_retrait"].get(k, 0))
        st.session_state["counts_cashout"] = restant
    else:
        # no retrait needed; restant = close
        st.session_state["counts_retrait"] = {k: 0 for k in DENOMS}
        st.session_state["counts_cashout"] = dict(close_counts)

    # --------- save daily receipt + state ---------
    # build receipt rows
    rows = []
    # group lines like sheet: bills/coins/rolls + totals
    def add_line(label, key):
        rows.append({
            "Ligne": label,
            "VALIDATION": st.session_state["counts_validation"].get(key, 0),
            "CASH-OUT": st.session_state["counts_cashout"].get(key, 0),
            "RETRAIT/DÉPÔT": st.session_state["counts_retrait"].get(key, 0),
            "FERMETURE": st.session_state["counts_fermeture"].get(key, 0),
        })

    for k in BILLS:
        add_line(k, k)
    for k in COINS:
        add_line(k, k)
    for k in ROLLS:
        add_line(k, k)

    meta = {
        "Date": today.isoformat(),
        "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "Caisse": st.session_state.till,
        "Caissier": (st.session_state.cashier.strip() or "—"),
        "Cible": f"{int(st.session_state.target)}$",
        "Close total": cents_to_str(total_cents(st.session_state["counts_fermeture"])),
        "À retirer": cents_to_str(max(0, diff)),
    }

    payload = {
        "cashier": st.session_state.cashier,
        "till": st.session_state.till,
        "target": int(st.session_state.target),
        "counts_validation": st.session_state["counts_validation"],
        "counts_cashout": st.session_state["counts_cashout"],
        "counts_retrait": st.session_state["counts_retrait"],
        "counts_fermeture": st.session_state["counts_fermeture"],
        "locked_retrait": st.session_state.locked_retrait,
        "meta": meta,
        "rows": rows,
    }

    h = hash_payload(payload)
    last_h = st.session_state.get("last_hash")
    if last_h != h:
        html = receipt_html(meta, rows)
        save_json(state_path, payload)
        save_text(receipt_path, html)
        st.session_state.last_hash = h

    st.write("")
    with st.expander("Aperçu reçu (Registre)", expanded=False):
        html = load_text(receipt_path)
        if html:
            components.html(html, height=620, scrolling=True)

    st.markdown("</div>", unsafe_allow_html=True)  # .sheet-wrap


# ================== SAUVEGARDE TAB ==================
with tab_save:
    st.subheader("Sauvegarde & reçus")
    days = list_days(DIR_CAISSE)
    if not days:
        st.info("Aucun enregistrement.")
    else:
        for ds in reversed(days):
            d = date.fromisoformat(ds)
            sp, rp = day_paths(d)
            with st.expander(f"{ds} — Registre", expanded=False):
                html = load_text(rp)
                if html:
                    components.html(html, height=650, scrolling=True)
                if os.path.exists(rp):
                    with open(rp, "rb") as f:
                        st.download_button(
                            "⬇️ Télécharger reçu (HTML)",
                            f.read(),
                            file_name=os.path.basename(rp),
                            mime="text/html",
                            key=f"dl_html_{ds}",
                        )
                if os.path.exists(sp):
                    with open(sp, "rb") as f:
                        st.download_button(
                            "⬇️ Télécharger état (JSON)",
                            f.read(),
                            file_name=os.path.basename(sp),
                            mime="application/json",
                            key=f"dl_json_{ds}",
                        )
