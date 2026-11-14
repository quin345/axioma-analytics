# AXIOMA terminal

The React analytics terminal, served by Flask at `/terminal/`. See the root
`README.md` for the whole system.

## Structure

- `src/main.tsx`, `src/App.tsx` - entry point and layout
- `src/api/` - typed `/api/*` client (`client.ts`, `endpoints.ts`, `types.ts`)
- `src/context/MarketContext.tsx` - selected instrument and window
- `src/hooks/` - polling, market data, card state, element sizing
- `src/sections/` - page sections (header, controls, analytics grid, footer, ...)
- `src/panels/` - individual analytics cards
- `src/charts/` - shared SVG chart primitives
- `src/lib/` - formatting, metrics, calendar and window helpers
- `src/assets.ts` - public asset URLs, prefixed with `import.meta.env.BASE_URL`
- `scripts/make_favicon.py` - regenerates the AXIOMA mark and icon set

## Conventions

- Built with `base: '/terminal/'`. Reference public files via `assets.ts`
  (or root-relative paths in `index.html`, which Vite prefixes), never a
  hard-coded `/assets/...`.
- API calls are absolute (`/api/...`) and are not affected by the base.
- Styling: Tailwind CSS v4 via `@tailwindcss/vite`; globals in `src/index.css`.

## Commands

```bash
pnpm install
pnpm dev      # http://localhost:5173, proxies /api to Flask on :8000
pnpm build    # -> dist/ (untracked); Flask reads it via TERMINAL_DIST
```
