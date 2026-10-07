# Spend Harmonizer: Data Quality Report

> All records are synthetic. Flags identify records for review; they do not prove an error.

## Run summary
- Harmonized rows: **1,231**
- Converted spend: **$1,063,538.66 USD**
- Resolved canonical suppliers: **14**
- Rows with one or more flags: **315**
- LLM path: **offline deterministic rules**

## Checks and counts
- Exact duplicate source rows: **62** (retained and flagged).
- Price outlier rows: **0** (3 x IQR within normalized item groups with at least four prices).
- Items with at least 50% max-to-min price spread: **1**.
- Supplier rows below the review threshold: **0**.
- Category rows below the review threshold: **0**.

### Missing values in source fields
| Field | Missing rows |
| --- | ---: |
| source_transaction_id | 0 |
| invoice_date | 0 |
| supplier_name | 0 |
| description | 0 |
| category | 241 |
| quantity | 0 |
| unit | 0 |
| unit_price | 0 |
| currency | 0 |
| region | 0 |

### Flag counts (flags may overlap)
| Flag | Rows |
| --- | ---: |
| `exact_duplicate` | 62 |
| `price_variance_review` | 36 |
| `source_category_missing` | 241 |

## Price review leads

Largest observed item-level spreads; compare like-for-like terms, regions, dates, and units before acting:

| Item | Observations | Suppliers | Regions | Min USD | Max USD | Spread | Observed premium vs low* |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Business laptop 14 inch | 36 | 14 | 6 | $865.47 | $1,374.43 | 58.8% | $28,149.38 |
| Cloud storage monthly subscription | 36 | 13 | 6 | $265.36 | $367.21 | 38.4% | $5,491.63 |
| Stretch wrap roll clear | 38 | 13 | 6 | $16.28 | $22.52 | 38.3% | $372.91 |
| Nitrile safety gloves box | 43 | 14 | 6 | $15.43 | $21.34 | 38.3% | $503.06 |
| Laboratory nitrile gloves box | 47 | 12 | 6 | $18.05 | $24.91 | 38.0% | $625.65 |
| Corrugated shipping box medium | 31 | 13 | 6 | $1.21 | $1.66 | 37.9% | $23.96 |
| A4 copy paper case 5 reams | 52 | 14 | 6 | $26.70 | $36.77 | 37.7% | $839.44 |
| Laptop dock USB-C | 37 | 14 | 6 | $150.47 | $207.13 | 37.7% | $3,500.79 |

*Observed premium is a screening calculation against the lowest recorded item unit price, multiplied by quantity. It is not validated or achievable savings.

## Method and limitations
- Currency conversion uses fixed illustrative demo rates: EUR 1.08 USD, GBP 1.27 USD, USD 1.00 USD. Replace with approved, period-aligned rates for real analysis.
- Duplicate detection checks exact duplicate source rows and retains them with a flag; it does not deduplicate transactions across systems.
- Supplier aliases and fuzzy matches are first-pass candidates. Categories and price comparisons also require source verification.
- Same-item comparisons normalize descriptions, but do not fully account for contract terms, delivery, tax, quality, or specifications.
- Synthetic data only. Do not load confidential procurement or personal data into this demo.
