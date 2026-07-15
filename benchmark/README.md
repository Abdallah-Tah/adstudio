# Gate 2 identity-consistency benchmark

Blocker for Phase 3 "Go": **at least 8 of 10 products must pass** the rubric
below across their complete scene set.

## Test set requirements

- 10 distinct products, 3–5 source photos each where available
- Include difficult cases: labels/logos, text-heavy packaging, reflective
  surfaces, transparent containers, irregular shapes, small products
- Distribute all five styles across the set (2 products per style)
- At least 5 scenes per product

Put photos in `benchmark/products/<product-slug>/` (jpg/png/webp) plus a
`description.txt` in each folder (one-paragraph product description).

## Run

```bash
./benchmark/run.sh            # creates projects, approves, batch-generates,
                              # writes contact_sheet_<project_id>.png per product
```

## Rubric — "identity holds" (fixed BEFORE inspecting results)

A scene passes only if ALL of:

1. Shape recognizable
2. Primary colors preserved
3. Packaging layout substantially preserved
4. Brand/logo not replaced
5. No invented attachments or product parts
6. Product remains the same category and physical form

A **product passes** when ≥80% of its scenes pass AND no scene shows a severe
identity substitution. "Most scenes acceptable" is not a pass by itself.

## Record results

Fill `benchmark/results.md` per product: style used, scenes pass/fail with the
failing rubric line, per-image cost, latency, regeneration count. Keep the
human review AND (Phase 3) the automated vision-QC results — this is the
baseline for judging whether Phase 3 improves or degrades identity.
