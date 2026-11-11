---
name: anti-slop-design
description: Always-on visual quality, anti-generic UI, accessibility, responsive HTML and evidence-based design review.
priority: 100
triggers: [html, design, dashboard, ui, ux, accessibility, responsive, wcag, frontend, rich card]
---
# Anti-slop design contract

Build the actual interface for its real job. Do not decorate a screenshot-shaped mockup. Before writing UI, identify the user, the primary task, the information hierarchy, the runtime constraints, and the interaction states.

## Visual craft
- Do not default to purple/blue gradients, glassmorphism, glow, oversized rounded cards, nested card grids, decorative blobs, emoji-as-icons, or generic SaaS hero layouts.
- Avoid card soup. Prefer spacing, alignment, typography, and restrained 1px separators. Use a container only when it creates a meaningful grouping.
- Never invent vanity metrics, ratings, status, user counts, uptime, or proof. Show only values backed by runtime data.
- Give each surface a clear hierarchy: one primary task, no competing headline, concise labels, readable data density, and real empty/error/loading states.
- Choose visual direction from the product context and existing brand; do not impose a fashionable style.
- Use local/system font stacks and inline CSS when embedded in WhatsApp. No dependency on Tailwind CDN, Google Fonts, remote CSS, icon fonts, or third-party script hosts.

## Accessibility and responsive behavior
- Use semantic landmarks, a single meaningful h1, ordered heading levels, real buttons/links, labels, and accessible names.
- Preserve visible keyboard focus; never remove outlines without a replacement. Use sufficient contrast and do not communicate state by color alone.
- Use `aria-live` only for concise dynamic status; avoid decorative ARIA. Give images useful alt text.
- Support narrow screens without horizontal overflow. Touch targets should be at least 44px where interactive.
- Respect `prefers-reduced-motion`; do not animate routine updates or use motion to hide state changes.
- Use real `<time datetime>` for time data and tabular numerals for counters.

## Delivery gate
For each interface change, verify the real states (empty, one item, many items, long labels, failure, disconnected realtime), keyboard flow, mobile width, console/runtime errors, and that displayed data comes from the backend. Report unverified platform behavior honestly.

## Provenance
This consolidated project rule is informed by Design Taste, Neej Frontend Craft, Microsoft Frontend Design Review, Addy Osmani Web Quality Accessibility, JPeetz Accessibility Compliance Audit, NVIDIA Agent Skills and the Agent Skills specification. See `skills/SOURCES.md` for source URLs and scope.
