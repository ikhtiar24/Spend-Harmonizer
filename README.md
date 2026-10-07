# Spend Harmonizer

This is a small, interview-friendly procurement spend harmonization demo built on synthetic data. It shows how fragmented ERP, corporate card, and manual tracker data can be cleaned, standardized, and reviewed for savings opportunities without exposing real procurement data.

## What this project does

- Loads three messy source files into a single staging table
- Normalizes supplier names, categories, dates, currencies, and units
- Uses an optional Anthropic Claude call when `ANTHROPIC_API_KEY` is present
- Falls back to deterministic fuzzy-matching logic when no API key is available, so the project runs fully offline
- Runs automated data-quality checks for duplicates, missing fields, outliers, and price variance
- Produces a harmonized CSV and a browser-ready dashboard

## Guardrails built in

- Synthetic data only: all data is fictional and intentionally messy
- Human-in-the-loop: the LLM is used as a first-pass assistant, not an auto-trust layer; low-confidence matches are flagged for review
- Repeatable prompting: all prompt templates are versioned in the `prompts/` folder
- Source verification: results are reviewed against source records before decisions are made

## Project structure

- `generate_synthetic_data.py` — creates seeded synthetic data and reference files
- `pipeline.py` — end-to-end harmonization and quality pipeline
- `prompts/` — reusable, versioned prompt templates for supplier resolution, category mapping, format standardization, and consistency review
- `data/raw/` — source CSVs
- `data/reference/` — canonical supplier and category reference files
- `outputs/` — harmonized CSV, quality report, and dashboard

## Run it

1. Open a terminal in the project folder.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the workflow:

   ```bash
   python pipeline.py
   ```

4. Open the dashboard in a browser:

   ```bash
   outputs/spend_dashboard.html
   ```

Optional Claude mode:

```bash
set ANTHROPIC_API_KEY=your_key_here
python pipeline.py
```

If no API key is set, the script automatically uses the deterministic offline path and logs that choice.

## Before / after summary

Before:

- Supplier names vary across ERP, card, and manual data
- Categories are incomplete or inconsistent
- Dates and currencies are mixed
- Duplicate and outlier rows are present
- Same item carries different unit prices across sources

After:

- Records are combined into a single harmonized view
- Supplier names resolve to canonical names with confidence flags
- Categories are mapped to a standard 2-level taxonomy
- Dates, units, and currencies are normalized
- Quality issues are surfaced in a reviewable report
- Savings levers are visible in the dashboard: fragmentation, variance, tail spend, and category concentration

## Expected outputs

- `outputs/spend_harmonized.csv` — cleaned, flat, Power BI-friendly table
- `outputs/data_quality_report.md` — summary of issues and counts
- `outputs/spend_dashboard.html` — self-contained HTML dashboard with charts

## Demo notes

This is intentionally small and readable. It is meant to show the workflow, the data-quality checks, and the value of harmonizing fragmented spend information quickly for stakeholder conversations and prototype reviews.
