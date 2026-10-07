# Slide 1 — The problem

## Fragmented procurement data hides spend leaks

- ERP, purchase card, and manual tracker records use different supplier spellings
- Product descriptions and categories are inconsistent
- Mixed currencies and date formats create poor comparability
- Duplicate rows and price variance make it hard to see actual savings opportunities

Key point: we have the data, but not the clean view needed for procurement decisions.

---

# Slide 2 — The pipeline

## Raw data → harmonization → reviewable output

1. Gather source exports into one staging table
2. Normalize supplier names, categories, dates, units, and currencies
3. Route through optional Claude prompts for first-pass classification and validation
4. Use deterministic fallback rules when no API key is configured
5. Run automated checks for duplicates, outliers, and price spread
6. Produce a harmonized CSV and a dashboard for review

Human review remains in the loop for low-confidence results.

---

# Slide 3 — Results and reusable prompts

## Savings opportunities become visible

- Supplier fragmentation shows categories with too many vendors
- Price variance highlights negotiation opportunities for the same item
- Tail spend shows small suppliers that may be rationalization targets
- Top categories reveal where spend is concentrated

Reusable prompt library:

- `supplier_resolution.md`
- `category_classification.md`
- `format_standardization.md`
- `consistency_check.md`

These are versioned templates designed for repeatable, explainable review workflows.
