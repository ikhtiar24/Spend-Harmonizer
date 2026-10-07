"""Harmonize fictional procurement exports and build reviewable demo outputs."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

from generate_synthetic_data import generate


ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
REFERENCE_DIR = ROOT / "data" / "reference"
PROMPT_DIR = ROOT / "prompts"
OUTPUT_DIR = ROOT / "outputs"
SOURCE_FILES = ["erp_export.csv", "pcard_export.csv", "manual_export.csv"]
REQUIRED_SOURCE_FIELDS = [
    "source_system", "source_transaction_id", "invoice_date", "supplier_name",
    "description", "category", "quantity", "unit", "unit_price", "currency", "region",
]
USD_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}
PRICE_VARIANCE_REVIEW_THRESHOLD = 0.50
SUPPLIER_REVIEW_THRESHOLD = 0.82
CATEGORY_REVIEW_THRESHOLD = 0.82


def _log(message: str) -> None:
    print(f"[Spend Harmonizer] {message}")


def _ensure_inputs() -> None:
    expected = [RAW_DIR / name for name in SOURCE_FILES] + [
        REFERENCE_DIR / "supplier_master.csv",
        REFERENCE_DIR / "category_taxonomy.csv",
    ]
    missing = [path for path in expected if not path.exists()]
    if missing:
        _log("Synthetic inputs are missing; regenerating the seeded sample dataset.")
        generate()


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"\s*#\s*\d+\s*$", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _clean_description(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(?:PURCHASE|POS|ONLINE ORDER|CARD TXN)\s*-\s*", "", text, flags=re.I)
    text = re.sub(r"\s*/\s*\d+.*$", "", text)
    text = re.sub(r"\s*\(urgent\)\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*-\s*dept order\s*$", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(left: str, right: str) -> float:
    left_key = _normalize_text(left)
    right_key = _normalize_text(right)
    if not left_key or not right_key:
        return 0.0
    sequence_score = SequenceMatcher(None, left_key, right_key).ratio()
    left_tokens, right_tokens = set(left_key.split()), set(right_key.split())
    token_score = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return max(sequence_score, token_score)


def _read_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source_frames = []
    for filename in SOURCE_FILES:
        frame = pd.read_csv(RAW_DIR / filename, dtype=str, keep_default_na=False)
        missing_columns = set(REQUIRED_SOURCE_FIELDS) - set(frame.columns)
        if missing_columns:
            raise ValueError(f"{filename} is missing required columns: {sorted(missing_columns)}")
        source_frames.append(frame[REQUIRED_SOURCE_FIELDS])
    staged = pd.concat(source_frames, ignore_index=True)
    suppliers = pd.read_csv(REFERENCE_DIR / "supplier_master.csv", dtype=str).fillna("")
    taxonomy = pd.read_csv(REFERENCE_DIR / "category_taxonomy.csv", dtype=str).fillna("")
    return staged, suppliers, taxonomy


def _call_claude(task: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    prompt_path = PROMPT_DIR / f"{task}.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    message = f"{prompt}\n\n## Runtime input\n{json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}"
    body = json.dumps({
        "model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        "max_tokens": 2500,
        "temperature": 0,
        "messages": [{"role": "user", "content": message}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = json.loads(response.read().decode("utf-8"))
        text = "\n".join(
            block.get("text", "") for block in response_body.get("content", [])
            if block.get("type") == "text"
        )
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Claude response did not contain a JSON object")
        result = json.loads(text[start:end + 1])
        version_match = re.search(r"## Version\s+([^\n]+)", prompt)
        prompt_version = version_match.group(1) if version_match else "unknown"
        _log(f"Claude completed {task} using prompt version {prompt_version}.")
        return result
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError) as error:
        _log(f"Claude {task} failed; deterministic rules will cover this step ({type(error).__name__}: {error}).")
        return None


def _build_llm_suggestions(
    staged: pd.DataFrame,
    suppliers: pd.DataFrame,
    taxonomy: pd.DataFrame,
) -> tuple[dict[str, tuple[str, float]], dict[str, tuple[str, str, float]], str | None]:
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        _log("LLM path: offline deterministic mode (ANTHROPIC_API_KEY is not set).")
        return {}, {}, None

    _log("LLM path: Claude enabled; deterministic checks remain the fallback and review gate.")
    supplier_counts = Counter(staged["supplier_name"].astype(str))
    supplier_values = [name for name, _ in supplier_counts.most_common(80) if name.strip()]
    supplier_payload = {
        "canonical_suppliers": suppliers["canonical_supplier"].tolist(),
        "known_aliases": [
            {"canonical_supplier": row.canonical_supplier, "aliases": row.known_aliases.split(" | ")}
            for row in suppliers.itertuples(index=False)
        ],
        "supplier_values": supplier_values,
    }
    supplier_response = _call_claude("supplier_resolution", supplier_payload)

    description_counts = Counter(_clean_description(value) for value in staged["description"])
    description_values = [name for name, _ in description_counts.most_common(100) if name]
    taxonomy_pairs = taxonomy[["category_level_1", "category_level_2"]].to_dict(orient="records")
    category_payload = {
        "taxonomy": taxonomy_pairs,
        "line_items": [
            {"input_id": value, "product_description": value, "source_category": ""}
            for value in description_values
        ],
    }
    category_response = _call_claude("category_classification", category_payload)

    sample_rows = staged.head(12).to_dict(orient="records")
    format_payload = {
        "records": [
            {
                "source_id": row["source_transaction_id"],
                "raw_date": row["invoice_date"],
                "currency": row["currency"],
                "unit": row["unit"],
                "quantity": row["quantity"],
                "unit_price": row["unit_price"],
            }
            for row in sample_rows
        ],
        "date_convention": {"ERP": "YYYY-MM-DD", "PCARD": "MM-DD-YYYY", "MANUAL": "DD/MM/YYYY or English month name"},
        "currency_rates_to_usd": {"rates": USD_RATES, "context": "illustrative fixed demo rates; not market rates"},
        "unit_map": {"ea": "each", "boxes": "box", "mo": "month", "licence": "license"},
    }
    format_response = _call_claude("format_standardization", format_payload)
    if format_response:
        _log("Claude format suggestions received; source parsing and USD conversion remain independently validated.")

    supplier_suggestions: dict[str, tuple[str, float]] = {}
    allowed_suppliers = set(suppliers["canonical_supplier"])
    if supplier_response:
        for mapping in supplier_response.get("mappings", []):
            canonical = mapping.get("canonical_supplier")
            try:
                confidence = float(mapping.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if mapping.get("input") in supplier_values and canonical in allowed_suppliers:
                supplier_suggestions[str(mapping["input"])] = (canonical, confidence)

    category_suggestions: dict[str, tuple[str, str, float]] = {}
    allowed_pairs = {(row["category_level_1"], row["category_level_2"]) for row in taxonomy_pairs}
    if category_response:
        for classification in category_response.get("classifications", []):
            category_pair = (classification.get("category_level_1"), classification.get("category_level_2"))
            try:
                confidence = float(classification.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if classification.get("input_id") in description_values and category_pair in allowed_pairs:
                category_suggestions[str(classification["input_id"])] = (*category_pair, confidence)

    return supplier_suggestions, category_suggestions, (format_response or {}).get("summary")


def _supplier_match(value: str, suppliers: pd.DataFrame) -> tuple[str, float]:
    if not value.strip():
        return "", 0.0
    aliases: list[tuple[str, str]] = []
    for row in suppliers.itertuples(index=False):
        aliases.append((row.canonical_supplier, row.canonical_supplier))
        aliases.extend((row.canonical_supplier, alias.strip()) for alias in row.known_aliases.split("|") if alias.strip())
    best_name, best_score = "", 0.0
    for canonical, alias in aliases:
        score = _similarity(value, alias)
        if score > best_score:
            best_name, best_score = canonical, score
    if best_score < 0.58:
        return "", best_score
    return best_name, round(best_score, 3)


def _category_from_raw(value: str, taxonomy: pd.DataFrame) -> tuple[str, str, float]:
    key = _normalize_text(value)
    if not key:
        return "", "", 0.0
    matches = []
    for row in taxonomy.itertuples(index=False):
        family_key = _normalize_text(row.category_level_1)
        subcategory_key = _normalize_text(row.category_level_2)
        if key == f"{family_key} {subcategory_key}" or key == f"{family_key} {subcategory_key}".replace(" ", ""):
            matches.append((row.category_level_1, row.category_level_2, 0.99))
        elif subcategory_key and (key == subcategory_key or subcategory_key in key.split()):
            matches.append((row.category_level_1, row.category_level_2, 0.91))
        elif key == family_key:
            matches.append((row.category_level_1, "", 0.64))
        elif family_key in key and subcategory_key in key:
            matches.append((row.category_level_1, row.category_level_2, 0.94))
    unique = {(family, subcategory) for family, subcategory, _ in matches}
    if len(unique) == 1:
        return matches[0]
    return "", "", 0.0


def _build_description_categories(staged: pd.DataFrame, taxonomy: pd.DataFrame) -> dict[str, tuple[str, str]]:
    votes: dict[str, Counter[tuple[str, str]]] = {}
    for row in staged.itertuples(index=False):
        category = _category_from_raw(row.category, taxonomy)
        description_key = _normalize_text(_clean_description(row.description))
        if description_key and category[0] and category[1]:
            votes.setdefault(description_key, Counter())[(category[0], category[1])] += 1
    return {key: counts.most_common(1)[0][0] for key, counts in votes.items()}


def _classify_category(
    description: str,
    raw_category: str,
    taxonomy: pd.DataFrame,
    description_categories: dict[str, tuple[str, str]],
) -> tuple[str, str, float]:
    direct_family, direct_subcategory, direct_confidence = _category_from_raw(raw_category, taxonomy)
    if direct_subcategory:
        return direct_family, direct_subcategory, direct_confidence
    description_key = _normalize_text(_clean_description(description))
    if description_key and description_categories:
        best_key = max(description_categories, key=lambda candidate: _similarity(description_key, candidate))
        score = _similarity(description_key, best_key)
        family, subcategory = description_categories[best_key]
        if score >= 0.70:
            return family, subcategory, round(min(0.96, score), 3)

    text = _normalize_text(_clean_description(description))
    keyword_rules = [
        ("laptop", "IT Hardware", "Laptops"),
        ("dock", "IT Hardware", "Laptops"),
        ("monitor", "IT Hardware", "Displays"),
        ("keyboard", "IT Hardware", "Peripherals"),
        ("headset", "IT Hardware", "Peripherals"),
        ("usb c adapter", "IT Hardware", "Peripherals"),
        ("collaboration", "Software & Cloud", "Software Licenses"),
        ("pdf editor", "Software & Cloud", "Software Licenses"),
        ("cloud storage", "Software & Cloud", "Cloud Services"),
        ("cloud compute", "Software & Cloud", "Cloud Services"),
        ("copy paper", "Office & MRO", "Office Supplies"),
        ("toner", "Office & MRO", "Office Supplies"),
        ("marker", "Office & MRO", "Office Supplies"),
        ("safety gloves", "Office & MRO", "Maintenance Supplies"),
        ("air filter", "Office & MRO", "Maintenance Supplies"),
        ("parcel delivery", "Logistics", "Freight Services"),
        ("pallet freight", "Logistics", "Freight Services"),
        ("shipping box", "Logistics", "Packaging"),
        ("stretch wrap", "Logistics", "Packaging"),
        ("cleaning", "Facilities & Energy", "Facilities Services"),
        ("electricity", "Facilities & Energy", "Energy"),
        ("consultant", "Professional Services", "Consulting"),
        ("safety glasses", "Safety & Lab", "Personal Protective Equipment"),
        ("laboratory", "Safety & Lab", "Laboratory Supplies"),
        ("mobile voice", "Telecom", "Telecom Services"),
        ("broadband", "Telecom", "Telecom Services"),
        ("vehicle lease", "Fleet", "Vehicle Leasing"),
        ("van lease", "Fleet", "Vehicle Leasing"),
        ("hvac", "Facilities & Energy", "Facilities Services"),
    ]
    for keyword, family, subcategory in keyword_rules:
        if keyword in text:
            return family, subcategory, 0.78
    if direct_family:
        return direct_family, "", direct_confidence
    return "", "", 0.0


def _parse_date(value: str, source_system: str) -> str:
    value = value.strip()
    formats = {
        "ERP": ["%Y-%m-%d", "%Y/%m/%d"],
        "PCARD": ["%m-%d-%Y", "%Y-%m-%d"],
        "MANUAL": ["%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"],
    }.get(source_system, ["%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y"])
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return ""


def _normalize_unit(value: str) -> str:
    key = _normalize_text(value)
    aliases = {
        "ea": "each", "each": "each", "pcs": "each", "piece": "each",
        "boxes": "box", "box": "box", "mo": "month", "months": "month", "month": "month",
        "licence": "license", "licenses": "license", "license": "license",
        "hrs": "hour", "hours": "hour", "hour": "hour",
    }
    return aliases.get(key, key)


def _float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _exact_duplicate_mask(staged: pd.DataFrame) -> pd.Series:
    return staged.duplicated(subset=REQUIRED_SOURCE_FIELDS, keep=False)


def _harmonize(
    staged: pd.DataFrame,
    suppliers: pd.DataFrame,
    taxonomy: pd.DataFrame,
    supplier_suggestions: dict[str, tuple[str, float]],
    category_suggestions: dict[str, tuple[str, str, float]],
) -> pd.DataFrame:
    description_categories = _build_description_categories(staged, taxonomy)
    duplicate_mask = _exact_duplicate_mask(staged)
    output_rows = []
    for index, row in staged.iterrows():
        flags: list[str] = []
        raw_supplier = str(row["supplier_name"])
        supplier_name, supplier_confidence = _supplier_match(raw_supplier, suppliers)
        suggestion = supplier_suggestions.get(raw_supplier)
        if suggestion and suggestion[1] >= 0.85:
            supplier_name, supplier_confidence = suggestion
        if not raw_supplier.strip():
            flags.append("missing_supplier")
        elif not supplier_name or supplier_confidence < SUPPLIER_REVIEW_THRESHOLD:
            flags.append("supplier_review")

        description = str(row["description"])
        raw_category = str(row["category"])
        category_family, category_subcategory, category_confidence = _classify_category(
            description, raw_category, taxonomy, description_categories
        )
        category_suggestion = category_suggestions.get(_clean_description(description))
        if category_suggestion and category_suggestion[2] >= 0.85:
            category_family, category_subcategory, category_confidence = category_suggestion
        if not raw_category.strip():
            flags.append("source_category_missing")
        if not category_family or not category_subcategory or category_confidence < CATEGORY_REVIEW_THRESHOLD:
            flags.append("category_review")

        source_system = str(row["source_system"]).strip().upper()
        transaction_date = _parse_date(str(row["invoice_date"]), source_system)
        quantity = _float(row["quantity"])
        unit_price = _float(row["unit_price"])
        currency = str(row["currency"]).strip().upper()
        unit_raw = str(row["unit"]).strip()
        if not transaction_date:
            flags.append("invalid_date")
        if quantity is None or quantity <= 0:
            flags.append("invalid_quantity")
        if unit_price is None or unit_price < 0:
            flags.append("invalid_unit_price")
        if currency not in USD_RATES:
            flags.append("unsupported_currency")
        if not unit_raw:
            flags.append("missing_unit")
        if not str(row["region"]).strip():
            flags.append("missing_region")
        if duplicate_mask.iloc[index]:
            flags.append("exact_duplicate")

        unit_price_usd = unit_price * USD_RATES[currency] if unit_price is not None and currency in USD_RATES else None
        spend_usd = unit_price_usd * quantity if unit_price_usd is not None and quantity is not None else None
        cleaned_description = _clean_description(description)
        output_rows.append({
            "source_system": source_system,
            "source_transaction_id": str(row["source_transaction_id"]).strip(),
            "transaction_date": transaction_date,
            "supplier_raw": raw_supplier,
            "supplier_canonical": supplier_name,
            "supplier_confidence": supplier_confidence,
            "product_raw": description,
            "product_name": cleaned_description,
            "item_key": _normalize_text(cleaned_description),
            "category_raw": raw_category,
            "category_level_1": category_family,
            "category_level_2": category_subcategory,
            "category_confidence": category_confidence,
            "quantity": quantity,
            "unit_raw": unit_raw,
            "unit": _normalize_unit(unit_raw),
            "unit_price_original": unit_price,
            "currency_original": currency,
            "unit_price_usd": unit_price_usd,
            "spend_usd": spend_usd,
            "region": str(row["region"]).strip(),
            "quality_flags": ";".join(flags),
            "needs_review": bool(flags),
        })

    harmonized = pd.DataFrame(output_rows)
    valid_prices = harmonized["unit_price_usd"].notna()
    for item_key, group in harmonized[valid_prices].groupby("item_key"):
        if len(group) < 4:
            continue
        q1, q3 = group["unit_price_usd"].quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr <= 0:
            continue
        outlier_indexes = group.index[(group["unit_price_usd"] < q1 - 3 * iqr) | (group["unit_price_usd"] > q3 + 3 * iqr)]
        harmonized.loc[outlier_indexes, "quality_flags"] = harmonized.loc[outlier_indexes, "quality_flags"].map(
            lambda value: f"{value};price_outlier" if value else "price_outlier"
        )

    price_min = harmonized.groupby("item_key")["unit_price_usd"].transform("min")
    price_max = harmonized.groupby("item_key")["unit_price_usd"].transform("max")
    spread = (price_max - price_min) / price_min.replace(0, pd.NA)
    harmonized.loc[spread >= PRICE_VARIANCE_REVIEW_THRESHOLD, "quality_flags"] = harmonized.loc[
        spread >= PRICE_VARIANCE_REVIEW_THRESHOLD, "quality_flags"
    ].map(lambda value: f"{value};price_variance_review" if value else "price_variance_review")
    harmonized["needs_review"] = harmonized["quality_flags"].ne("")
    return harmonized


def _price_variance(harmonized: pd.DataFrame) -> pd.DataFrame:
    comparable = harmonized.dropna(subset=["unit_price_usd"]).copy()
    result = comparable.groupby("item_key", dropna=False).agg(
        product_name=("product_name", "first"),
        observations=("unit_price_usd", "size"),
        supplier_count=("supplier_canonical", "nunique"),
        region_count=("region", "nunique"),
        min_price_usd=("unit_price_usd", "min"),
        max_price_usd=("unit_price_usd", "max"),
        average_price_usd=("unit_price_usd", "mean"),
    ).reset_index()
    result["spread_pct"] = result["max_price_usd"] / result["min_price_usd"].replace(0, pd.NA) - 1
    lowest = comparable.groupby("item_key")["unit_price_usd"].transform("min")
    comparable["premium_vs_low_usd"] = (comparable["unit_price_usd"] - lowest).clip(lower=0) * comparable["quantity"].fillna(0)
    premium = comparable.groupby("item_key")["premium_vs_low_usd"].sum()
    result["observed_premium_vs_low_usd"] = result["item_key"].map(premium).fillna(0)
    return result.sort_values("spread_pct", ascending=False)


def _quality_metrics(harmonized: pd.DataFrame, staged: pd.DataFrame) -> dict[str, Any]:
    flag_series = harmonized["quality_flags"].fillna("").str.split(";").explode()
    flag_counts = flag_series[flag_series.ne("")].value_counts().to_dict()
    price_variance = _price_variance(harmonized)
    currency_counts = staged["currency"].str.upper().value_counts().to_dict()
    missing_fields = {
        field: int(staged[field].astype(str).str.strip().eq("").sum())
        for field in ["source_transaction_id", "invoice_date", "supplier_name", "description", "category", "quantity", "unit", "unit_price", "currency", "region"]
    }
    return {
        "row_count": int(len(harmonized)),
        "spend_total_usd": float(harmonized["spend_usd"].fillna(0).sum()),
        "supplier_count": int(harmonized["supplier_canonical"].replace("", pd.NA).nunique()),
        "duplicate_rows": int(flag_counts.get("exact_duplicate", 0)),
        "missing_fields": missing_fields,
        "flag_counts": {str(key): int(value) for key, value in flag_counts.items()},
        "price_outlier_rows": int(flag_counts.get("price_outlier", 0)),
        "price_variance_items": int((price_variance["spread_pct"] >= PRICE_VARIANCE_REVIEW_THRESHOLD).sum()),
        "price_variance_threshold": PRICE_VARIANCE_REVIEW_THRESHOLD,
        "low_confidence_supplier_rows": int(flag_counts.get("supplier_review", 0)),
        "low_confidence_category_rows": int(flag_counts.get("category_review", 0)),
        "currency_counts": {str(key): int(value) for key, value in currency_counts.items()},
    }


def _build_dashboard(harmonized: pd.DataFrame, metrics: dict[str, Any], variance: pd.DataFrame) -> Path:
    category_summary = harmonized.groupby("category_level_1", dropna=False).agg(
        spend_usd=("spend_usd", "sum"), suppliers=("supplier_canonical", "nunique")
    ).reset_index().sort_values("spend_usd", ascending=False)
    fragmentation = harmonized.dropna(subset=["category_level_1"]).groupby("category_level_1").agg(
        suppliers=("supplier_canonical", "nunique"), spend_usd=("spend_usd", "sum")
    ).reset_index().sort_values("suppliers", ascending=False)
    supplier_spend = harmonized[harmonized["supplier_canonical"].ne("")].groupby("supplier_canonical")["spend_usd"].sum().sort_values(ascending=False)
    spend_total = float(supplier_spend.sum())
    tail_suppliers = supplier_spend[ supplier_spend / max(spend_total, 1) <= 0.01 ]
    price_table = variance.head(12).copy()

    dashboard_data = {
        "totalSpend": metrics["spend_total_usd"],
        "rowCount": metrics["row_count"],
        "supplierCount": metrics["supplier_count"],
        "reviewRows": int(harmonized["needs_review"].sum()),
        "currencyCounts": metrics["currency_counts"],
        "fragmentation": [
            {"category": str(row.category_level_1), "suppliers": int(row.suppliers), "spend": round(float(row.spend_usd or 0), 2)}
            for row in fragmentation.itertuples(index=False)
        ],
        "categories": [
            {"category": str(row.category_level_1), "spend": round(float(row.spend_usd or 0), 2)}
            for row in category_summary.itertuples(index=False)
        ],
        "tail": {
            "supplierCount": int(len(tail_suppliers)),
            "supplierShare": round(100 * len(tail_suppliers) / max(len(supplier_spend), 1), 1),
            "spend": round(float(tail_suppliers.sum()), 2),
            "spendShare": round(100 * float(tail_suppliers.sum()) / max(spend_total, 1), 1),
            "thresholdPct": 1,
        },
        "priceVariance": [
            {
                "item": str(row.product_name),
                "spreadPct": round(float(row.spread_pct) * 100, 1) if pd.notna(row.spread_pct) else 0,
                "min": round(float(row.min_price_usd), 2),
                "max": round(float(row.max_price_usd), 2),
                "suppliers": int(row.supplier_count),
                "regions": int(row.region_count),
                "observations": int(row.observations),
                "premium": round(float(row.observed_premium_vs_low_usd), 2),
            }
            for row in price_table.itertuples(index=False)
        ],
    }

    data_json = json.dumps(dashboard_data, ensure_ascii=True).replace("<", "\\u003c")
    html = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spend Harmonizer | Procurement levers</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
:root{--ink:#18312f;--muted:#637775;--paper:#f2f5ef;--white:#fff;--line:#dce5df;--green:#24715b;--lime:#b7d746;--coral:#df674f;--blue:#4381a4;--gold:#d19d37;--font:Trebuchet MS,Segoe UI,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 var(--font)}header{background:#18312f;color:#f5f7ef;padding:28px max(24px,calc((100vw - 1320px)/2)) 24px;position:relative;overflow:hidden}header:after{content:"";position:absolute;right:8%;top:-92px;width:250px;height:250px;border:1px solid #72958a;border-radius:50%;box-shadow:0 0 0 26px #ffffff08,0 0 0 54px #ffffff06} .eyebrow{color:#b7d746;text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:1px}h1{font:normal 32px/1.1 Georgia,serif;margin:8px 0 5px}header p{color:#ccdbd4;margin:0;max-width:620px}.wrap{max-width:1320px;margin:auto;padding:22px 24px 44px}.topline{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px;color:var(--muted);font-size:12px}.source-pill{border:1px solid #b9c8bf;padding:5px 9px;border-radius:3px;background:#fff8}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.kpi{background:var(--white);border:1px solid var(--line);border-top:3px solid var(--green);padding:13px 15px;min-width:0}.kpi:nth-child(2){border-top-color:var(--blue)}.kpi:nth-child(3){border-top-color:var(--gold)}.kpi:nth-child(4){border-top-color:var(--coral)}.kpi label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;font-weight:bold}.kpi strong{display:block;font:normal 25px Georgia,serif;margin-top:4px;overflow-wrap:anywhere}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.panel{background:var(--white);border:1px solid var(--line);padding:15px 16px 14px;min-width:0}.panel h2{font:normal 19px Georgia,serif;margin:0 0 2px}.panel .sub{color:var(--muted);font-size:12px;margin:0 0 10px;min-height:34px}.chartbox{height:238px;position:relative}.tail-copy{font:normal 18px Georgia,serif;margin:8px 0 4px}.tail-detail{color:var(--muted);font-size:12px}.table-wrap{max-height:210px;overflow:auto;margin-top:8px}table{border-collapse:collapse;width:100%;font-size:11px}th,td{text-align:left;padding:6px 5px;border-bottom:1px solid #e8eeea;white-space:nowrap}th{color:var(--muted);font-weight:700;position:sticky;top:0;background:white}td:first-child{white-space:normal;min-width:128px}.note{color:var(--muted);font-size:11px;border-top:1px solid var(--line);padding-top:12px;margin-top:16px}.status{font-size:12px;color:var(--muted)}@media(max-width:780px){.grid{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}header:after{right:-170px}.wrap{padding:16px 14px 32px}}@media(max-width:420px){h1{font-size:27px}.topline{align-items:flex-start;flex-direction:column}.kpi strong{font-size:21px}}
</style>
</head>
<body>
<header><div class="eyebrow">Synthetic procurement analysis · FY2025</div><h1>Spend, made comparable.</h1><p>One harmonized view across ERP, card, and manual purchasing records. Flags are review leads, not confirmed errors or promised savings.</p></header>
<main class="wrap">
<div class="topline"><span>Fixed illustrative FX: EUR 1.08 · GBP 1.27 to USD</span><span class="source-pill">Offline-generated · Synthetic only</span></div>
<section class="kpis"><div class="kpi"><label>Analyzed spend</label><strong id="totalSpend">$0</strong></div><div class="kpi"><label>Source transactions</label><strong id="rowCount">0</strong></div><div class="kpi"><label>Resolved suppliers</label><strong id="supplierCount">0</strong></div><div class="kpi"><label>Rows flagged for review</label><strong id="reviewRows">0</strong></div></section>
<section class="grid">
<article class="panel"><h2>Supplier fragmentation</h2><p class="sub">Distinct resolved suppliers by category. More suppliers in a category can indicate a consolidation discussion.</p><div class="chartbox"><canvas id="fragmentationChart" aria-label="Supplier counts by category"></canvas></div></article>
<article class="panel"><h2>Same-item price spread</h2><p class="sub">Maximum-to-minimum observed unit-price spread in USD across suppliers and regions.</p><div class="chartbox"><canvas id="varianceChart" aria-label="Price spread by item"></canvas></div><div class="table-wrap"><table><thead><tr><th>Item</th><th>Range (USD)</th><th>Suppliers</th><th>Regions</th><th>Observed premium*</th></tr></thead><tbody id="varianceTable"></tbody></table></div></article>
<article class="panel"><h2>Tail spend</h2><p class="sub">Suppliers individually representing at most 1% of resolved supplier spend.</p><div class="chartbox"><canvas id="tailChart" aria-label="Tail supplier spend"></canvas></div><div class="tail-copy" id="tailSummary"></div><div class="tail-detail" id="tailDetail"></div></article>
<article class="panel"><h2>Top spend categories</h2><p class="sub">Harmonized category-level spend in USD, ranked by total.</p><div class="chartbox"><canvas id="categoryChart" aria-label="Spend by category"></canvas></div></article>
</section>
<p class="note">*Observed premium compares recorded quantity-weighted prices with the lowest observed unit price for that normalized item across all regions. It is a screening signal, not an addressable savings estimate. Rows with uncertain mappings, exact duplicates, or price checks remain in the data and are flagged.</p>
<p class="status" id="chartStatus" role="status"></p>
</main>
<script>
const data = __DASHBOARD_DATA__;
const money = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value||0);
document.getElementById('totalSpend').textContent=money(data.totalSpend);
document.getElementById('rowCount').textContent=new Intl.NumberFormat().format(data.rowCount);
document.getElementById('supplierCount').textContent=new Intl.NumberFormat().format(data.supplierCount);
document.getElementById('reviewRows').textContent=new Intl.NumberFormat().format(data.reviewRows);
document.getElementById('tailSummary').textContent=`${data.tail.supplierCount} suppliers · ${data.tail.supplierShare}% of supplier base · ${money(data.tail.spend)} spend`;
document.getElementById('tailDetail').textContent=`That is ${data.tail.spendShare}% of analyzed resolved-supplier spend at the <= ${data.tail.thresholdPct}% per-supplier threshold.`;
document.getElementById('varianceTable').innerHTML=data.priceVariance.map(row=>`<tr><td>${row.item}</td><td>${money(row.min)}–${money(row.max)} (${row.spreadPct}%)</td><td>${row.suppliers}</td><td>${row.regions}</td><td>${money(row.premium)}</td></tr>`).join('');
if(typeof Chart==='undefined'){document.getElementById('chartStatus').textContent='Chart library did not load. The data remains embedded; reconnect to the internet to render charts.';}else{
const common={responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#637775'}},y:{grid:{color:'#edf1ed'},ticks:{color:'#637775'}}}};
new Chart(document.getElementById('fragmentationChart'),{type:'bar',data:{labels:data.fragmentation.map(x=>x.category),datasets:[{data:data.fragmentation.map(x=>x.suppliers),backgroundColor:'#4381a4',borderRadius:2}]},options:{...common,indexAxis:'y',scales:{x:{beginAtZero:true,grid:{color:'#edf1ed'},ticks:{precision:0}},y:{grid:{display:false}}}}});
new Chart(document.getElementById('varianceChart'),{type:'bar',data:{labels:data.priceVariance.slice(0,10).map(x=>x.item),datasets:[{data:data.priceVariance.slice(0,10).map(x=>x.spreadPct),backgroundColor:'#df674f',borderRadius:2}]},options:{...common,indexAxis:'y',scales:{x:{beginAtZero:true,title:{display:true,text:'Spread (%)'},grid:{color:'#edf1ed'}},y:{grid:{display:false},ticks:{autoSkip:false}}}}});
new Chart(document.getElementById('tailChart'),{type:'doughnut',data:{labels:['Tail suppliers','Other suppliers'],datasets:[{data:[data.tail.spend,Math.max(0,data.totalSpend-data.tail.spend)],backgroundColor:['#d19d37','#24715b'],borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{position:'bottom',labels:{usePointStyle:true,boxWidth:8}}}}});
new Chart(document.getElementById('categoryChart'),{type:'bar',data:{labels:data.categories.map(x=>x.category),datasets:[{data:data.categories.map(x=>x.spend),backgroundColor:'#24715b',borderRadius:2}]},options:{...common,indexAxis:'y',scales:{x:{beginAtZero:true,grid:{color:'#edf1ed'},ticks:{callback:value=>money(value)}},y:{grid:{display:false}}}}});
}
</script>
</body>
</html>'''.replace("__DASHBOARD_DATA__", data_json)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "spend_dashboard.html"
    path.write_text(html, encoding="utf-8")
    return path


def _write_report(
    metrics: dict[str, Any],
    harmonized: pd.DataFrame,
    variance: pd.DataFrame,
    claude_summary: str | None,
    llm_enabled: bool,
) -> Path:
    flags = metrics["flag_counts"]
    lines = [
        "# Spend Harmonizer: Data Quality Report",
        "",
        "> All records are synthetic. Flags identify records for review; they do not prove an error.",
        "",
        "## Run summary",
        f"- Harmonized rows: **{metrics['row_count']:,}**",
        f"- Converted spend: **${metrics['spend_total_usd']:,.2f} USD**",
        f"- Resolved canonical suppliers: **{metrics['supplier_count']}**",
        f"- Rows with one or more flags: **{int(harmonized['needs_review'].sum()):,}**",
        f"- LLM path: **{'Claude enabled (per-task fallback on errors)' if llm_enabled else 'offline deterministic rules'}**",
        "",
        "## Checks and counts",
        f"- Exact duplicate source rows: **{metrics['duplicate_rows']}** (retained and flagged).",
        f"- Price outlier rows: **{metrics['price_outlier_rows']}** (3 x IQR within normalized item groups with at least four prices).",
        f"- Items with at least {metrics['price_variance_threshold']:.0%} max-to-min price spread: **{metrics['price_variance_items']}**.",
        f"- Supplier rows below the review threshold: **{metrics['low_confidence_supplier_rows']}**.",
        f"- Category rows below the review threshold: **{metrics['low_confidence_category_rows']}**.",
        "",
        "### Missing values in source fields",
        "| Field | Missing rows |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {field} | {count} |" for field, count in metrics["missing_fields"].items())
    lines.extend(["", "### Flag counts (flags may overlap)", "| Flag | Rows |", "| --- | ---: |"])
    lines.extend(f"| `{flag}` | {count} |" for flag, count in sorted(flags.items()))
    lines.extend(["", "## Price review leads", "", "Largest observed item-level spreads; compare like-for-like terms, regions, dates, and units before acting:", ""])
    lines.extend(["| Item | Observations | Suppliers | Regions | Min USD | Max USD | Spread | Observed premium vs low* |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for row in variance.head(8).itertuples(index=False):
        lines.append(
            f"| {row.product_name} | {row.observations} | {row.supplier_count} | {row.region_count} | "
            f"${row.min_price_usd:,.2f} | ${row.max_price_usd:,.2f} | {row.spread_pct:.1%} | ${row.observed_premium_vs_low_usd:,.2f} |"
        )
    lines.extend([
        "",
        "*Observed premium is a screening calculation against the lowest recorded item unit price, multiplied by quantity. It is not validated or achievable savings.",
        "",
        "## Method and limitations",
        "- Currency conversion uses fixed illustrative demo rates: EUR 1.08 USD, GBP 1.27 USD, USD 1.00 USD. Replace with approved, period-aligned rates for real analysis.",
        "- Duplicate detection checks exact duplicate source rows and retains them with a flag; it does not deduplicate transactions across systems.",
        "- Supplier aliases and fuzzy matches are first-pass candidates. Categories and price comparisons also require source verification.",
        "- Same-item comparisons normalize descriptions, but do not fully account for contract terms, delivery, tax, quality, or specifications.",
        "- Synthetic data only. Do not load confidential procurement or personal data into this demo.",
    ])
    if claude_summary:
        lines.extend(["", "## Claude consistency-check summary", "", claude_summary, ""])
    path = OUTPUT_DIR / "data_quality_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run() -> None:
    _ensure_inputs()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    staged, suppliers, taxonomy = _read_inputs()
    _log(f"Loaded and appended {len(staged):,} source rows from {len(SOURCE_FILES)} exports.")
    supplier_suggestions, category_suggestions, claude_summary = _build_llm_suggestions(staged, suppliers, taxonomy)
    harmonized = _harmonize(staged, suppliers, taxonomy, supplier_suggestions, category_suggestions)
    variance = _price_variance(harmonized)
    metrics = _quality_metrics(harmonized, staged)

    csv_path = OUTPUT_DIR / "spend_harmonized.csv"
    harmonized.to_csv(csv_path, index=False, encoding="utf-8", float_format="%.4f")
    report_path = _write_report(
        metrics, harmonized, variance, claude_summary,
        bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
    )
    dashboard_path = _build_dashboard(harmonized, metrics, variance)
    _log(f"Wrote {csv_path.relative_to(ROOT)} ({len(harmonized):,} rows).")
    _log(f"Wrote {report_path.relative_to(ROOT)} and {dashboard_path.relative_to(ROOT)}.")
    _log(
        f"Review summary: {int(harmonized['needs_review'].sum()):,} flagged rows, "
        f"{metrics['duplicate_rows']} exact duplicates, {metrics['price_variance_items']} high-variance items."
    )


if __name__ == "__main__":
    run()