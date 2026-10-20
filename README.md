# Maps Lead Finder — Local Docker + Live Vercel

This is **one codebase** for both workflows:

- **Local:** Docker Compose runs FastAPI + PostgreSQL in containers.
- **Live:** Vercel runs the FastAPI app; a hosted PostgreSQL database (for example Neon) stores the same business records.

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
3. Edit `.env` and set your API key and a strong local DB password:
   ```dotenv
   GOOGLE_MAPS_API_KEY=your_google_places_api_key
   POSTGRES_PASSWORD=replace_with_a_strong_local_password
   APP_PORT=8000
   DISCOVER_SOCIAL_LINKS=true
   ```
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
4. Deploy. If you change environment variables later, redeploy.
5. Check `https://YOUR-PROJECT.vercel.app/api/health`, then open the site's root URL and test searching, updating contact status, refreshing, and exporting CSV.

## C. Important: local and production data are separate

- Docker Compose uses its own local PostgreSQL container, with persistent volume `postgres_data`.
- Vercel uses the hosted database configured in `DATABASE_URL`.
- Records do not automatically sync between these databases. Use the CSV export/import workflow if you need to move data.
- Both environments use the same Google Places API key only if you configure it in each environment. Consider separate restricted keys and usage limits.

## Limits and production safety

- Places Text Search is not an unrestricted export of every listing. The app requests up to 60 results for a query, subject to Google API behavior and terms.
- Social links are best-effort links found on the business website homepage. Review response time is not supplied by the standard Places API and is shown as unavailable.
- Vercel serverless functions have execution limits. Searching and visiting many websites for social links can time out on larger searches.
- This starter does not include user authentication or per-user access control. Do not expose it publicly for real lead data until you add authentication, authorization, rate limiting, and production hardening.
- Restrict the Google API key, enable billing alerts/quotas, and follow Google Maps Platform terms, attribution, and retention requirements.
