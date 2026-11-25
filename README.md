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
