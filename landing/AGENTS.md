# AXIOMA landing page

The public product landing page, served by Flask at `/`. It started as a Figma
Make export; Figma-specific tooling has been removed. See the root `README.md`
for the whole system.

## Structure

- `src/App.tsx` - the whole page (header, hero, mock terminal, sections, footer, access dialog)
- `src/index.css` - Tailwind v4 import plus the page's hand-written styles
- `public/assets/*.svg` - page artwork, served at `/assets/...`
- `public/assets/axioma-mark.svg`, `public/favicon.*`, `public/icon-*.png` - copies of the terminal's logo and icon set (`terminal/scripts/make_favicon.py`); keep them identical

## Conventions

- Links into the product point at `/terminal/` (a separate Vite app). Do not
  hard-link to `/terminal/assets/...`.
- Built output goes to `dist/` (untracked); Flask reads it via `LANDING_DIST`.

## Commands

```bash
pnpm install
pnpm dev      # http://localhost:5174
pnpm build    # -> dist/
```
