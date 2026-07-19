# Gate 2 identity benchmark — results

## electric-lice-vacuum-comb (2026-07-19 — offline preflight only)

First product photos received. Live generation not yet run (no API keys in the
test environment). Deterministic stages verified against the real photos:

- **Reference classification**: front (primary, q=0.842), side (q=0.844),
  detail_closeup (q=0.819), angle (q=0.732). No resolution/aspect warnings.
- **Segmentation**: rembg could not run in the sandbox (u2net weights download
  blocked by network policy) — must be re-run on the Pi. A stand-in matte fed
  through `refine_cutout` confirmed the refinement path: 19 raw components
  reduced to the product body, coverage 0.285, quality 0.78, valid as a
  generation reference; `segmentation_fragmented` warning fires as expected.
- **Scene routing** (STRICT lock, valid cutout present): static hero/packaging
  and chamber-macro scenes -> `composite_exact_product`; hand-held/hair
  scenes -> `hybrid`; physically-impossible scene -> `reference_generation`.
  Without a valid cutout every scene correctly falls back to
  `reference_generation`.

**Caveat for the live run**: the four supplied photos are marketing/listing
composites (text overlays, multiple views, instruction panels). Usable for
routing tests, but clean single-product shots on a plain background are needed
for good cutouts and compositing. Requested from Abdallah: front, side, back,
comb-teeth detail, buttons/display detail — no text, one product per frame.

| product | style | scenes pass | severe substitution | per-image cost | regen count |
|---|---|---|---|---|---|
| electric-lice-vacuum-comb | pending live run | — | — | — | — |
