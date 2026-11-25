# Lab Secret Santa 🎅

Streamlit app: people register via a link (name, email, age, sex), the organizer runs a random draw
(nobody gets themselves) and the app emails everyone their match. The background image is `assets/background.jpg` (replace it to change the look). Matches are never shown on screen –
not even to the organizer.

## Run it

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then edit it
streamlit run app.py --server.address 0.0.0.0
```

Share `http://<your-machine-or-server>:8501` with the lab (same network / VPN). Data lives in
`secret_santa.db` next to the app (set `SANTA_DB=/path/file.db` to move it).

## Organizer workflow

1. Open the app, log in via the **sidebar** with `admin_password`.
2. **Setup & invites**: set event name, date, maximum gift value (default 10 €), extra text; optionally email the link to a list of addresses.
3. **Draw & emails**: send yourself a test email, then **Run the draw** (needs ≥ 3 people; closes registration), then **Send pending emails**.
   Failed ones stay "not sent" and can be retried or resent individually.
4. After the event: **Cleanup → Delete all data**.

## Putting it on GitHub (public repo)

A GitHub repo only stores the code – it does not run the app or send emails. To get the app online you need a host
that runs it, e.g. **Streamlit Community Cloud** (deploys straight from a GitHub repo) or a lab server.

- The `.gitignore` already excludes `.streamlit/secrets.toml` and `*.db`. **Check with `git status` that neither is
  committed** – a public repo must never contain the admin password, SMTP password or participant data.
- On Streamlit Community Cloud, paste the contents of your `secrets.toml` into *App settings → Secrets* instead.
- The app URL will be public, so anyone with the link can register. Only the organizer panel is password-protected.
- Community Cloud's disk is not permanent: an app restart can wipe `secret_santa.db`. Keep the registration window
  short, download the participants CSV (Participants tab) as a backup, and run the draw + send emails in one sitting.
- Sending: Community Cloud servers are outside the university network, so an institute SMTP server may refuse them.
  A normal mail provider with an app password (e.g. Gmail) usually works; test with the *Send test email* button.

## Notes

- **SMTP**: fill in `[smtp]` in `secrets.toml` (port 587 = STARTTLS, 465 = SSL). Use your institute's mail server or a
  dedicated account; app-specific passwords are best. Never commit `secrets.toml`.
- **Hosting**: a lab server or your own machine is safest for the data (see the GitHub section above for cloud caveats).
- **Privacy (GDPR)**: the form has a consent checkbox, email is never shared, age/sex only go into the match email
  (switchable off in the Draw tab) and everything can be deleted afterwards.
- Registrants can't edit entries themselves; the organizer can remove someone (before the draw) and they re-register.
