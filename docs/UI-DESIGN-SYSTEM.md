# Kwee UI: Velvet Noir / Feminine Comic Editorial

## Design read

Kwee remains an adult field journal, now with a warmer feminine editorial palette: plum ink, blush paper, rose accents, and champagne details. The live panel stays compact and legible; the final dossier switches to a deep-plum, magazine-like closing page so completion feels distinct from activity.

## Visual tokens

- Ink: `#321D32`; live paper: `#FFF0F3`; final field: `#2B192D`.
- Rose: `#D85D83`; champagne: `#F1D4AD`; lilac: `#DDC9E5`; sage: `#C8DFCF`.
- Use thick outlines and offset shadows sparingly, rounded editorial panels, a restrained halftone texture, and clear type hierarchy.
- No glassmorphism, neon glow, floating fake metrics, or decorative visual elements without a job.

## Native Markdown contract

The GenAI Markdown primitive remains responsible for paragraphs, bold/italic/strikethrough, inline and fenced code, links/autolinks, ordered and nested lists, task lists, blockquotes, compact tables, math, and citations. `markdown-layout.js` segments headings, horizontal rules, and GitHub-style callouts only. Task-list source markers are preserved instead of being replaced with Unicode glyphs.

Wide/verbose tables are converted into labeled records for phone readability. Short tables remain native tables.

## Accessibility and behavior

- Semantic content remains text, not an image of text.
- Meaning is not encoded by color alone; callouts include a label/icon.
- Runtime card supports light/dark schemes and reduced motion.
- Dynamic runtime values are HTML-escaped before template substitution.
- Keep reading order linear and preserve link/citation affordances.

## Rendered preview fixtures

- `ui-previews/progress-live.png`: blush/plum active workroom panel with phase, progress bar, and event log.
- `ui-previews/final-dossier.png`: deep-plum closing dossier with execution record and summary metrics.
- `ui-previews/runtime-card.png`: compact runtime status panel.

These are browser-rendered visual fixtures from representative test data, not live runtime telemetry.
