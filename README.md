# AppointHive Sales OS (Maps Lead Finder)

Internal AppointHive sales workspace: **Discover** (Google Places) plus **pipeline, tasks, proposals, invoices, and payments**.

This is **one codebase** for both workflows:

- **Local:** Docker Compose runs FastAPI + PostgreSQL in containers.
- **Live:** Vercel runs the FastAPI app; a hosted PostgreSQL database (for example Neon) stores the same business records.

```bash
# Tests (synthetic DB)
pytest tests/ -v
```

Local docs / QA notes live under `support/` (gitignored; not part of the shipped product tree).

> Vercel does not run the PostgreSQL container from Docker Compose. Use a hosted PostgreSQL database for production.

## A. Run locally with Docker

### Requirements
- Docker Desktop (or Docker Engine + Compose)
- Google Places API (New) key with Places API enabled and billing configured

### Start
1. Extract the project ZIP and open a terminal in the `maps-lead-finder` folder.
2. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` and set your API key, a strong local DB password, and login secrets:
   ```dotenv
   GOOGLE_MAPS_API_KEY=your_google_places_api_key
   POSTGRES_PASSWORD=replace_with_a_strong_local_password
   APP_PORT=8000
   DISCOVER_SOCIAL_LINKS=true
   LOGIN_PASSWORD=replace_with_a_strong_login_password
   SESSION_SECRET=replace_with_a_long_random_secret
   ```
   Sign in with username `hello@appointhive.com` and the `LOGIN_PASSWORD` value. There is no signup.
4. Start the stack:
   ```bash
   docker compose up --build -d
   ```
5. Open http://localhost:8000. Health check: http://localhost:8000/api/health

### Useful Docker commands
```bash
docker compose logs -f app
docker compose ps
docker compose down
```
To stop containers without deleting the saved PostgreSQL volume, run `docker compose down`. **Do not** use `docker compose down -v` unless you intend to delete the local database volume.

## B. Deploy the same project to Vercel

### 1. Create a hosted PostgreSQL database
1. Create a database at https://console.neon.tech/ (or another hosted PostgreSQL provider).
2. Copy its connection string. For this SQLAlchemy setup, use a URL in this format:
   ```text
   postgresql+psycopg://USER:PASSWORD@HOST/DATABASE?sslmode=require
   ```
   Use the real values from the provider. Keep this URL secret.

### 2. Push this folder to GitHub
1. Create a repository on https://github.com/.
2. Upload the **contents** of this project folder (including `index.py`, `vercel.json`, `app/`, and `requirements.txt`).
3. Do not commit `.env`, API keys, or database credentials. `.gitignore` excludes `.env`.

### 3. Import to Vercel
1. Go to https://vercel.com/new and import the GitHub repository.
2. Set the project root to the repository root (the folder containing `vercel.json`).
3. In Project → Settings → Environment Variables, add:
   - `DATABASE_URL` = the hosted PostgreSQL URL from Neon
   - `GOOGLE_MAPS_API_KEY` = your Google Places API key
   - `DISCOVER_SOCIAL_LINKS` = `true` (optional; can be `false` to disable website scanning)
   - `LOGIN_PASSWORD` = password for username `hello@appointhive.com`
   - `SESSION_SECRET` = a long random string used to sign the login cookie
   - `BLOB_READ_WRITE_TOKEN` = Vercel Blob store token (demo uploads; prefer Blob — S3 not required)
   - Stripe/PayPal keys = **optional** (Owner confirm works without them)
4. Deploy. If you change environment variables later, redeploy.
5. Check `https://YOUR-PROJECT.vercel.app/api/health`, then open the site's root URL, sign in with `hello@appointhive.com`, and test searching, updating contact status, refreshing, and exporting CSV.

## C. Important: local and production data are separate

- Docker Compose uses its own local PostgreSQL container, with persistent volume `postgres_data`.
- Vercel uses the hosted database configured in `DATABASE_URL`.
- Records do not automatically sync between these databases. Use the CSV export/import workflow if you need to move data.
- Both environments use the same Google Places API key only if you configure it in each environment. Consider separate restricted keys and usage limits.

## Limits and production safety

- Places Text Search is not an unrestricted export of every listing. The app requests up to 60 results for a query, subject to Google API behavior and terms.
- Social links are best-effort links found on the business website homepage. Review response time is not supplied by the standard Places API and is shown as unavailable.
- Vercel serverless functions have execution limits. Searching and visiting many websites for social links can time out on larger searches.
- The app uses a single shared login (`hello@appointhive.com` + `LOGIN_PASSWORD`) with a signed session cookie. There is no signup and no per-user data isolation. Add stronger auth, rate limiting, and production hardening before wider exposure.
- Restrict the Google API key, enable billing alerts/quotas, and follow Google Maps Platform terms, attribution, and retention requirements.
