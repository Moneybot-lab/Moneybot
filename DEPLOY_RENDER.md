# Render Deployment Steps (MoneyBot)

## 0) Quick fix for `No matching distribution found for install`
If your Render logs show:

```text
ERROR: Could not find a version that satisfies the requirement install
ERROR: No matching distribution found for install
```

your Build Command is malformed (it usually contains `pip install --upgrade pip install ...`).

Set **Build Command** to exactly:

```bash
bash scripts/render_build.sh
```

Then redeploy using **Clear build cache & deploy**.

## 1) Create services
1. Create a **PostgreSQL** service in Render.
2. Create a **Web Service** for this repo.

## 2) Environment variables
Set these env vars on the web service:
- `PYTHON_VERSION` = `3.11.11` (prevents Render from selecting Python 3.14, which breaks `numba`-based builds)
- `DATABASE_URL` = internal connection string from Render Postgres
- `MONEYBOT_SECRET_KEY` = long random stable secret (do not rotate frequently)
- `DATA_PROVIDER` = `yfinance` (optional; defaults to `yfinance`)
- `FINNHUB_API_KEY` = your Finnhub key (optional, enables Finnhub quote source before yfinance fallback)
  - MoneyBot also accepts `FINNHUB_TOKEN` or `X_FINNHUB_TOKEN` for compatibility with different secret naming conventions.

## 3) Install + migrate + run (Render Web Service)
Configure the web service with the following commands:

- **Build Command**
  ```bash
  bash scripts/render_build.sh
  ```

  The script runs:
  - `python -m pip install --upgrade pip`
  - `python -m pip install -r requirements.txt`
  - `python -m pip install Flask-SQLAlchemy Flask-Migrate SQLAlchemy psycopg2-binary "psycopg[binary]"`

- **Start Command**
  ```bash
  bash -lc 'if [ -d migrations ]; then flask --app app:app db upgrade || echo "Migration step failed; starting web server anyway."; else echo "No migrations directory found; skipping database migration step."; fi; python app.py'
  ```

Notes:
- `db upgrade` applies committed Alembic migrations when a `migrations/` folder exists.
- If `migrations/` is missing, startup now skips migrations instead of failing the deploy.
- The start command runs `python app.py` as the long-running process.

## 4) First-time migration bootstrap (local/dev)
If your repo does **not** already contain a `migrations/` folder, initialize it once locally:

```bash
export MONEYBOT_SECRET_KEY='dev-secret'
export DATABASE_URL='postgresql://<user>:<password>@<host>:5432/<db_name>'

flask --app app:app db init
flask --app app:app db migrate -m "initial schema"
flask --app app:app db upgrade
```

Then commit the generated migrations:

```bash
git add migrations
git commit -m "Add initial database migrations"
```

After that, deploy to Render and keep using step 3 (`db upgrade && gunicorn`) for ongoing deploys.

## 5) If deploy logs still show missing psycopg/psycopg2
- Ensure your **actual Render Build Command** is exactly:
  - `bash scripts/render_build.sh`
- Common failure: `pip install --upgrade pip install -r requirements.txt` (note the extra `install`) makes pip try to install a package literally named `install`, which matches the error `No matching distribution found for install`.
- If your service still shows the same `No matching distribution found for install` log after this repo change, your Render dashboard is still overriding the repo command. Remove the custom build command in the UI (or set it to `bash scripts/render_build.sh`) and redeploy.
- If you set custom commands in the Render dashboard, they override repo defaults from `render.yaml`.
- A missing Postgres driver forces MoneyBot to fall back to SQLite, which means login/portfolio data will not persist across deploys.

## 6) Exact UI recovery steps for the `No matching distribution found for install` error
1. Open **Render Dashboard → moneybot-pro → Settings**.
2. In **Build Command**, remove any custom inline pip command.
3. Set Build Command to exactly:
   ```bash
   bash scripts/render_build.sh
   ```
4. Confirm it is **not**:
   ```bash
   pip install --upgrade pip install -r requirements.txt
   ```
   (that command is malformed and causes pip to search for a package named `install`).
5. Click **Save Changes**.
6. Go to **Manual Deploy** and choose **Clear build cache & deploy**.
7. In build logs, verify you see the three expected commands from `scripts/render_build.sh`.

## 7) Verify Finnhub is actually being used (after deploy)
Run these checks against production after setting the Finnhub env var:

```bash
curl -s "https://<your-service>.onrender.com/api/quote?symbol=TSLA" | jq .data
```

Look for:
- `quote_source: "finnhub"` on success
- or `quote_source: "yfinance"` with diagnostics containing:
  - `finnhub_attempted`
  - `finnhub_key_source`
  - `finnhub_error`

This makes it explicit whether Finnhub auth was missing/invalid or the service intentionally fell back to yfinance.


For quick triage from the UI-backed endpoint, you can also inspect:
```bash
curl -s "https://<your-service>.onrender.com/api/quick-ask?symbol=TSLA" | jq .data
```
Look for `quote_source` and `quote_diagnostics` to confirm whether Finnhub was used or why fallback occurred.

## 8) If requests contain `symbol=/api/quote?symbol=TSLA`
MoneyBot now normalizes URL-like symbol input on API endpoints (e.g. quick-ask, quote, signal, watchlist add).
If logs still show symbols like `/API/QUOTE?SYMBOL=TSLA`, redeploy the latest commit so normalization is live.

Quick check:
```bash
curl -s "https://<your-service>.onrender.com/api/quick-ask?symbol=%2Fapi%2Fquote%3Fsymbol%3DTSLA" | jq .data.symbol
```
Expected output:
```
"TSLA"
```
