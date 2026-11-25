"""Lab Secret Santa – a small Streamlit app.

Participants register via the app link (name, email, age, sex).
The organizer logs in (sidebar), runs the draw and sends the match emails.
Nobody – including the organizer – sees who drew whom unless they open
their own email.
"""

from __future__ import annotations

import base64
import os
import random
import re
import smtplib
import sqlite3
import ssl
import time
from contextlib import closing
from datetime import datetime
from email.message import EmailMessage
from hmac import compare_digest

import pandas as pd
import streamlit as st

DB_PATH = os.environ.get("SANTA_DB", "secret_santa.db")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SEX_OPTIONS = ["female", "male", "diverse", "prefer not to say"]

st.set_page_config(page_title="Lab Secret Santa", page_icon="🎅", layout="centered",
                   initial_sidebar_state="collapsed")


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@st.cache_resource
def init_db() -> bool:
    with closing(db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS participants (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                age        INTEGER,
                sex        TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assignments (
                giver_id    INTEGER PRIMARY KEY REFERENCES participants(id) ON DELETE CASCADE,
                receiver_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                sent_at     TEXT,
                error       TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
    return True


DEFAULT_SETTINGS = {
    "event_name": "Lab Secret Santa",
    "event_date": "",
    "max_gift_eur": "10",
    "extra_message": "",
    "registration_open": "1",
    "drawn": "0",
}


def get_settings() -> dict[str, str]:
    with closing(db()) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


def set_settings(**kwargs: str) -> None:
    with closing(db()) as conn, conn:
        for k, v in kwargs.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(v)),
            )


def list_participants() -> list[sqlite3.Row]:
    with closing(db()) as conn:
        return conn.execute("SELECT * FROM participants ORDER BY name COLLATE NOCASE").fetchall()


# --------------------------------------------------------------------------
# Draw logic
# --------------------------------------------------------------------------
def make_derangement(ids: list[int], rng: random.Random | None = None) -> dict[int, int]:
    """Random assignment giver -> receiver where nobody draws themselves."""
    if len(ids) < 3:
        raise ValueError("Need at least 3 participants for a Secret Santa draw.")
    rng = rng or random.SystemRandom()
    while True:  # succeeds with probability ~1/e per attempt
        perm = list(ids)
        rng.shuffle(perm)
        if all(g != r for g, r in zip(ids, perm)):
            return dict(zip(ids, perm))


def run_draw() -> int:
    people = list_participants()
    mapping = make_derangement([p["id"] for p in people])
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM assignments")
        conn.executemany(
            "INSERT INTO assignments(giver_id, receiver_id) VALUES(?, ?)", mapping.items()
        )
    set_settings(drawn="1", registration_open="0")
    return len(mapping)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
def smtp_config() -> dict | None:
    try:
        cfg = dict(st.secrets["smtp"])
    except Exception:
        return None
    if not cfg.get("host"):
        return None
    cfg["port"] = int(cfg.get("port", 587))
    cfg.setdefault("from_addr", cfg.get("user", ""))
    cfg.setdefault("from_name", "Lab Secret Santa")
    return cfg if cfg["from_addr"] else None


def open_smtp(cfg: dict) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        server = smtplib.SMTP_SSL(cfg["host"], 465, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
    if cfg.get("user"):
        server.login(cfg["user"], cfg.get("password", ""))
    return server


def new_message(cfg: dict, to_addr: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_addr"]}>'
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def match_email_body(giver_name: str, receiver: dict, s: dict[str, str], include_details: bool) -> str:
    lines = [f"Hi {giver_name},", "", f"You are the Secret Santa for: {receiver['name']}", ""]
    if include_details:
        bits = []
        if receiver.get("age"):
            bits.append(f"age: {receiver['age']}")
        if receiver.get("sex") and receiver["sex"] != "prefer not to say":
            bits.append(f"sex: {receiver['sex']}")
        if bits:
            lines += [f"A little hint for gift ideas – {', '.join(bits)}.", ""]
    if s.get("max_gift_eur"):
        lines.append(f"Maximum gift value: 5-10 €")
    if s.get("event_date"):
        lines.append(f"Gift exchange: {s['event_date']}")
    if s.get("extra_message"):
        lines += ["", s["extra_message"]]
    lines += ["", "Please keep it secret! 🤫", "", f"– {s.get('event_name') or 'Lab Secret Santa'}"]
    return "\n".join(lines)


def send_pending_matches(include_details: bool, only_giver: int | None = None) -> tuple[int, int]:
    """Send match emails that have not been sent yet. Returns (sent, failed)."""
    cfg = smtp_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured (see README / secrets.toml).")
    s = get_settings()
    q = """
        SELECT a.giver_id, g.name AS giver_name, g.email AS giver_email,
               r.name AS r_name, r.age AS r_age, r.sex AS r_sex
        FROM assignments a
        JOIN participants g ON g.id = a.giver_id
        JOIN participants r ON r.id = a.receiver_id
        WHERE {where}
    """
    with closing(db()) as conn:
        if only_giver is None:
            rows = conn.execute(q.format(where="a.sent_at IS NULL")).fetchall()
        else:
            rows = conn.execute(q.format(where="a.giver_id = ?"), (only_giver,)).fetchall()
    sent = failed = 0
    if not rows:
        return 0, 0
    server = open_smtp(cfg)
    try:
        for row in rows:
            receiver = {"name": row["r_name"], "age": row["r_age"], "sex": row["r_sex"]}
            body = match_email_body(row["giver_name"], receiver, s, include_details)
            msg = new_message(cfg, row["giver_email"], f"🎅 {s['event_name']} – your match", body)
            try:
                server.send_message(msg)
                err, ts = None, datetime.now().isoformat(timespec="seconds")
                sent += 1
            except Exception as exc:  # keep going; record the failure
                err, ts = str(exc)[:300], None
                failed += 1
            with closing(db()) as conn, conn:
                conn.execute(
                    "UPDATE assignments SET sent_at = ?, error = ? WHERE giver_id = ?",
                    (ts, err, row["giver_id"]),
                )
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent, failed


def send_plain_emails(items: list[tuple[str, str, str]]) -> tuple[int, list[str]]:
    """items = [(to, subject, body)]. Returns (sent, errors)."""
    cfg = smtp_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured (see README / secrets.toml).")
    server = open_smtp(cfg)
    sent, errors = 0, []
    try:
        for to, subject, body in items:
            try:
                server.send_message(new_message(cfg, to, subject, body))
                sent += 1
            except Exception as exc:
                errors.append(f"{to}: {exc}")
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent, errors


# --------------------------------------------------------------------------
# Look & feel
# --------------------------------------------------------------------------
def set_background() -> None:
    """Photo background + a card that follows the viewer's light/dark mode."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "background.jpg")
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    st.markdown(
        f"""
        <style>
        [data-testid="stAppViewContainer"] {{
            background: url("data:image/jpeg;base64,{b64}") center bottom / cover fixed no-repeat;
        }}
        [data-testid="stHeader"] {{ background: transparent; }}
        [data-testid="stMainBlockContainer"], .block-container {{
            background: rgba(255, 255, 255, 0.88);
            border-radius: 18px;
            padding: 2rem 2.5rem 2.5rem 2.5rem !important;
            margin-top: 1.5rem;
            box-shadow: 0 6px 30px rgba(40, 40, 90, 0.25);
        }}
        /* Streamlit follows the OS/browser setting, so the card does too. */
        @media (prefers-color-scheme: dark) {{
            [data-testid="stMainBlockContainer"], .block-container {{
                background: rgba(20, 22, 34, 0.88);
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Public page: registration
# --------------------------------------------------------------------------
def page_register() -> None:
    s = get_settings()
    st.title(f"🎅 {s['event_name']}")
    meta = []
    if s["event_date"]:
        meta.append(f"📅 {s['event_date']}")
    if s["max_gift_eur"]:
        meta.append(f"💶 Maximum gift value: **{s['max_gift_eur']} €**")
    if meta:
        st.markdown("  \n".join(meta))

    if s["drawn"] == "1":
        st.info("The draw has already happened and registration is closed. Check your inbox for your match! 🎁")
        return
    if s["registration_open"] != "1":
        st.warning("Registration is currently closed. Please ask the organizer.")
        return

    st.write("Sign up below. Once everyone has registered, names are drawn at random and everybody "
             "gets an email with the person they are gifting.")

    with st.form("register", clear_on_submit=True):
        name = st.text_input("Name", max_chars=80)
        email = st.text_input("Email", max_chars=120)
        c1, c2 = st.columns(2)
        age = c1.number_input("Age", min_value=16, max_value=99, value=None, step=1)
        sex = c2.selectbox("Sex", SEX_OPTIONS, index=None, placeholder="Select…")
        consent = st.checkbox(
            "I agree that my name, email, age and sex are stored for this Secret Santa only. "
            "Age and sex may be shared with my Secret Santa as a gift hint; email is never shared. "
            "Data is deleted after the event."
        )
        submitted = st.form_submit_button("Join Secret Santa 🎁", type="primary")

    if submitted:
        name, email = name.strip(), email.strip().lower()
        if not name:
            st.error("Please enter your name.")
        elif not EMAIL_RE.match(email):
            st.error("Please enter a valid email address.")
        elif age is None or sex is None:
            st.error("Please fill in age and sex.")
        elif not consent:
            st.error("Please tick the consent box.")
        else:
            try:
                with closing(db()) as conn, conn:
                    conn.execute(
                        "INSERT INTO participants(name, email, age, sex, created_at) VALUES(?,?,?,?,?)",
                        (name, email, int(age), sex, datetime.now().isoformat(timespec="seconds")),
                    )
                st.success(f"You're in, {name}! 🎄 You'll get an email once the draw is done.")
            except sqlite3.IntegrityError:
                st.error("This email address is already registered.")

    st.caption(f"{len(list_participants())} people registered so far.")


# --------------------------------------------------------------------------
# Organizer panel
# --------------------------------------------------------------------------
def admin_login() -> bool:
    """Sidebar login. Returns True when authenticated."""
    with st.sidebar:
        st.header("Organizer")
        if st.session_state.get("is_admin"):
            if st.button("Log out"):
                st.session_state["is_admin"] = False
                st.rerun()
            return True
        try:
            expected = str(st.secrets["admin_password"])
        except Exception:
            st.caption("Admin password not configured (see README).")
            return False
        pw = st.text_input("Password", type="password", key="admin_pw")
        if st.button("Log in"):
            if compare_digest(pw.encode(), expected.encode()):
                st.session_state["is_admin"] = True
                st.rerun()
            time.sleep(1)  # slow down guessing
            st.error("Wrong password.")
    return False


def tab_setup(s: dict[str, str]) -> None:
    st.subheader("Event details")
    with st.form("settings"):
        event_name = st.text_input("Event name", s["event_name"])
        event_date = st.text_input("Date / place of gift exchange", s["event_date"],
                                   placeholder="e.g. 17 Dec 2026, 15:00, seminar room")
        max_gift = st.number_input("Maximum gift value (€)", min_value=0, max_value=500, step=1,
                                   value=int(s["max_gift_eur"] or 10))
        extra = st.text_area("Extra text for the match email", s["extra_message"])
        reg_open = st.checkbox("Registration open", s["registration_open"] == "1")
        if st.form_submit_button("Save", type="primary"):
            set_settings(event_name=event_name, event_date=event_date, max_gift_eur=str(int(max_gift)),
                         extra_message=extra, registration_open="1" if reg_open else "0")
            st.success("Saved.")
            st.rerun()

    st.subheader("Send invitations")
    st.caption("Or simply share the app link yourself (chat, mailing list).")
    app_url = st.text_input("Link to this app", placeholder="http://<server>:8501")
    emails_raw = st.text_area("Email addresses (one per line or comma-separated)")
    if st.button("Send invitation emails"):
        addrs = sorted({a.strip().lower() for a in re.split(r"[,\n;]+", emails_raw) if a.strip()})
        bad = [a for a in addrs if not EMAIL_RE.match(a)]
        if not app_url.strip():
            st.error("Please enter the app link.")
        elif not addrs or bad:
            st.error(f"Invalid or missing addresses: {', '.join(bad) or 'none given'}")
        else:
            body = (f"Hi!\n\nYou're invited to join our {s['event_name']}.\n"
                    f"Please sign up here: {app_url.strip()}\n")
            if s["max_gift_eur"]:
                body += f"\nMaximum gift value: {s['max_gift_eur']} €"
            if s["event_date"]:
                body += f"\nWhen: {s['event_date']}"
            try:
                sent, errors = send_plain_emails([(a, f"🎅 Join our {s['event_name']}", body) for a in addrs])
                st.success(f"Sent {sent} invitation(s).")
                for e in errors:
                    st.error(e)
            except Exception as exc:
                st.error(f"Could not send: {exc}")


def tab_participants(s: dict[str, str]) -> None:
    people = list_participants()
    st.metric("Registered", len(people))
    if people:
        df = pd.DataFrame([dict(p) for p in people]).drop(columns=["id"])
        st.dataframe(df, width="stretch", hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False), "participants.csv", "text/csv")
    if s["drawn"] == "1":
        st.caption("The draw is done – participants can no longer be removed (reset the draw first).")
    elif people:
        st.subheader("Remove a participant")
        choice = st.selectbox("Participant", people, format_func=lambda p: f"{p['name']} <{p['email']}>",
                              index=None, placeholder="Select…")
        if choice and st.button("Remove"):
            with closing(db()) as conn, conn:
                conn.execute("DELETE FROM participants WHERE id = ?", (choice["id"],))
            st.rerun()


def tab_draw(s: dict[str, str]) -> None:
    people = list_participants()
    cfg = smtp_config()
    if cfg is None:
        st.warning("SMTP is not configured – you can run the draw, but not send emails. See README.")

    include_details = st.checkbox("Include the match's age and sex in the email as a gift hint", value=True)

    with st.expander("Preview of the email (example data)"):
        st.code(match_email_body("Alex", {"name": "Sam", "age": 29, "sex": "female"}, s, include_details))

    if cfg:
        test_to = st.text_input("Send a test email to (your own address)")
        if st.button("Send test email") and EMAIL_RE.match(test_to.strip()):
            try:
                body = match_email_body("Alex", {"name": "Sam (TEST)", "age": 29, "sex": "female"}, s, include_details)
                sent, errs = send_plain_emails([(test_to.strip(), "TEST – Secret Santa", body)])
                st.success("Test email sent.") if sent else st.error(errs[0])
            except Exception as exc:
                st.error(f"SMTP error: {exc}")

    st.divider()
    if s["drawn"] != "1":
        st.write(f"**{len(people)}** participants registered.")
        if len(people) < 3:
            st.info("You need at least 3 participants.")
        elif st.button("🎲 Run the draw", type="primary"):
            run_draw()
            st.rerun()
        return

    st.success("The draw is done. Nobody (including you) can see the matches here.")
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.email, a.sent_at, a.error FROM assignments a "
            "JOIN participants g ON g.id = a.giver_id ORDER BY g.name COLLATE NOCASE"
        ).fetchall()
    status = pd.DataFrame(
        [{"name": r["name"], "email": r["email"],
          "status": "✅ sent " + r["sent_at"] if r["sent_at"] else ("❌ " + r["error"] if r["error"] else "⏳ not sent")}
         for r in rows]
    )
    st.dataframe(status, width="stretch", hide_index=True)

    pending = sum(1 for r in rows if not r["sent_at"])
    if cfg and pending and st.button(f"✉️ Send {pending} pending email(s)", type="primary"):
        with st.spinner("Sending…"):
            try:
                sent, failed = send_pending_matches(include_details)
                (st.success if not failed else st.warning)(f"Sent: {sent}, failed: {failed}")
            except Exception as exc:
                st.error(f"SMTP error: {exc}")
        st.rerun()

    if cfg and rows:
        with st.expander("Resend to one person"):
            who = st.selectbox("Person", rows, format_func=lambda r: f"{r['name']} <{r['email']}>",
                               index=None, placeholder="Select…")
            if who and st.button("Resend"):
                try:
                    sent, failed = send_pending_matches(include_details, only_giver=who["id"])
                    st.success("Resent.") if sent else st.error("Failed – see status table.")
                except Exception as exc:
                    st.error(f"SMTP error: {exc}")
                st.rerun()

    with st.expander("⚠️ Reset the draw"):
        st.caption("Deletes all matches so you can draw again. Anyone already emailed will have an outdated match!")
        if st.checkbox("I understand") and st.button("Reset draw"):
            with closing(db()) as conn, conn:
                conn.execute("DELETE FROM assignments")
            set_settings(drawn="0")
            st.rerun()


def tab_cleanup() -> None:
    st.write("After the event, delete all personal data (names, emails, age, sex, matches).")
    if st.checkbox("Yes, delete everything") and st.button("Delete all data", type="primary"):
        with closing(db()) as conn, conn:
            conn.execute("DELETE FROM assignments")
            conn.execute("DELETE FROM participants")
            conn.execute("DELETE FROM settings WHERE key IN ('drawn','registration_open')")
        st.success("All participant data deleted.")
        st.rerun()


def page_admin() -> None:
    s = get_settings()
    st.title("🎅 Organizer panel")
    t1, t2, t3, t4 = st.tabs(["Setup & invites", "Participants", "Draw & emails", "Cleanup"])
    with t1:
        tab_setup(s)
    with t2:
        tab_participants(s)
    with t3:
        tab_draw(s)
    with t4:
        tab_cleanup()


# --------------------------------------------------------------------------
def main() -> None:
    init_db()
    set_background()
    if admin_login():
        page_admin()
    else:
        page_register()


if __name__ == "__main__":
    main()
