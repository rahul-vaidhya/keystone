# Veratas — Frontend

Vite + React SPA with JWT auth and a role-aware app shell.

## Quick start

1. Start Postgres + Redis (from repo root): `docker compose up -d`
2. Run backend migrations and API (see backend README / root instructions)
3. Install and run the SPA:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — sign up to create an org (you become **owner**), or sign in.

> **Port 8000 note:** on this machine port 8000 is sometimes occupied by an unrelated
> process (not this repo). Run the backend on **8010** instead and update the Vite proxy
> target in `vite.config.ts` to match — see the backend README's Local dev section.

## Auth

- **Signup** → creates organization + owner user, returns JWT access token
- **Login** → email + password; refresh token stored in httpOnly cookie
- **Roles:** `owner` | `admin` | `member` (shown in header badge)

The Vite dev server proxies `/auth` and `/health` to the FastAPI backend
(target configured in `vite.config.ts`, currently port **8010**).

## Main app

After login, `/app` shows the home screen (chat placeholder, Phase 4) and, for
owners/admins, a team/users management page.
