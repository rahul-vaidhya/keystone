# uitokens.md — Design Primitives (LEAN — thin SPA)

> This is a backend-heavy product. The SPA is presentational, so this file is intentionally
> small. Expand only if the UI grows. Map names to the actual CSS variables / Tailwind config.

## Color (dark-first; add light later if needed)
- `--bg`            #0b0b0f      (app background)
- `--surface`       #16161c      (cards, panels)
- `--border`        #2a2a33
- `--text`          #e7e7ea
- `--text-muted`    #9a9aa3
- `--accent`        #3b82f6      (primary actions, links, citation highlight)
- `--success`       #22c55e      (status: ready)
- `--warning`       #f59e0b      (status: parsing/embedding)
- `--danger`        #ef4444      (status: failed)

## Spacing — strict 4px grid
`space-1`=4px · `space-2`=8px · `space-3`=12px · `space-4`=16px · `space-6`=24px · `space-8`=32px

## Radius
`radius-sm`=4px · `radius-md`=8px · `radius-lg`=12px

## Typography
- Sans: system UI stack. Mono: for citations, IDs, debug bundle.
- Sizes: `text-sm`=14 · `text-base`=16 · `text-lg`=18 · `text-xl`=20. Line-height 1.5 for body.

## Rule
**No hard-coded hex values or pixel spacing in components** — reference these tokens only.
