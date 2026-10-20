# Vercel deployment

For the full guide covering **both local Docker and live Vercel deployment**, see `README.md`.

Quick outline:
1. Create hosted PostgreSQL at https://console.neon.tech/.
2. Push the contents of this folder to GitHub.
3. Import the repository at https://vercel.com/new.
4. Add `DATABASE_URL`, `GOOGLE_MAPS_API_KEY`, and optional `DISCOVER_SOCIAL_LINKS` environment variables.
5. Deploy and test `/api/health`.

Vercel does not run the PostgreSQL service from `docker-compose.yml`; local Docker and hosted Vercel use separate databases.
