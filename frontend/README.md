# SEPA AI — Next.js Frontend

Next.js 14 dashboard for the Minervini SEPA screener (FastAPI backend).

---

## Quick Deploy to Vercel

1. **Push code to GitHub** (the `frontend/` folder is part of the mono-repo).

2. **Import project in Vercel**
   - Go to [vercel.com](https://vercel.com) → **Import Project**
   - Select your GitHub repo
   - Set **Root Directory** → `frontend`
   - Framework preset will auto-detect **Next.js**

3. **Set environment variables** in Vercel dashboard → Settings → Environment Variables:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | `https://your-server-ip-or-domain:8000` |
   | `NEXT_PUBLIC_API_KEY` | your FastAPI read key |

4. **Deploy** → Vercel builds and gives you a public URL.

> The `vercel.json` rewrite proxies `/api/*` → your FastAPI server to avoid CORS
> issues and keep the API key out of the browser. Update the `destination` URL in
> `vercel.json` to match your server before deploying.

---

## Local Development

```bash
cd frontend
npm install
cp .env.example .env.local   # fill in your API URL + key
npm run dev                  # http://localhost:3000
```

Make sure FastAPI is running at `http://localhost:8000`:

```bash
# From project root
make api
```

---

## Production Build Test

```bash
cd frontend
npm run build
npm run start          # serves at http://localhost:3000
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | FastAPI base URL |
| `NEXT_PUBLIC_API_KEY` | *(empty)* | API read key (`X-API-Key` header) |

Copy `.env.example` → `.env.local` for local dev. Never commit real keys.

---

## Project Structure

```
frontend/
├── app/                  # Next.js App Router pages
│   ├── layout.tsx        # Root layout (NavBar, fonts, favicon)
│   ├── page.tsx          # Dashboard
│   ├── screener/         # Stock screener
│   ├── watchlist/        # Watchlist manager
│   └── portfolio/        # Paper trading portfolio
├── components/           # Shared UI components
│   ├── NavBar.tsx
│   ├── StockTable.tsx
│   ├── CandlestickChart.tsx
│   ├── Skeleton.tsx      # Loading skeleton variants
│   ├── ApiOfflineBanner.tsx  # API health error banner
│   └── ...
├── lib/
│   ├── api.ts            # Typed API client
│   └── types.ts          # Shared TypeScript types
├── .env.example          # Template — copy to .env.local
├── .env.production       # Production template
├── next.config.ts        # API proxy rewrite
└── vercel.json           # Vercel deployment config
```

---

## Makefile Shortcuts (from project root)

```bash
make frontend-dev      # npm run dev
make frontend-build    # npm run build
make frontend-deploy   # npx vercel --prod
```
