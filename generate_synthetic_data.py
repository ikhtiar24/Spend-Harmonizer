"""Generate repeatable, fictional procurement exports for the demo."""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
REFERENCE_DIR = ROOT / "data" / "reference"
SEED = 20261008

SUPPLIERS = [
    ("Siemens AG", ["Siemens AG", "siemens", "Siemens GmbH", "SIEMENS", "Siem. AG"]),
    ("Acme Industrial Supplies", ["Acme Industrial Supplies", "ACME Ind.", "Acme Supply Co", "ACME"]),
    ("Northstar Office Products", ["Northstar Office Products", "North Star Office", "NORTHSTAR", "Northstar Co."]),
    ("Vertex Technology Ltd", ["Vertex Technology Ltd", "Vertex Tech", "VERTEX TECHNOLOGY", "Vertex Ltd."]),
    ("Global Freight Partners", ["Global Freight Partners", "Global Freight", "GFP Logistics", "Global Frt. Ptnrs"]),
    ("Bluebird Facilities Group", ["Bluebird Facilities Group", "Blue Bird Facilities", "Bluebird FM", "BLUEBIRD"]),
    ("Pinnacle Safety Equipment", ["Pinnacle Safety Equipment", "Pinnacle Safety", "Pinnacle PPE", "Pinnacle Equip."]),
    ("Meridian Energy Services", ["Meridian Energy Services", "Meridian Energy", "Meridian Utilities", "MERIDIAN"]),
    ("Apex Packaging Europe", ["Apex Packaging Europe", "Apex Packaging", "APEX Pack.", "Apex Pkg Europe"]),
    ("Cedar Consulting Group", ["Cedar Consulting Group", "Cedar Consulting", "CEDAR CG", "Cedar Consult."]),
    ("Harbor Scientific Supply", ["Harbor Scientific Supply", "Harbour Scientific", "Harbor Sci.", "HSS"]),
    ("Redwood Telecom Services", ["Redwood Telecom Services", "Redwood Telecom", "Redwood Telco", "REDWOOD"]),
    ("Summit Vehicle Leasing", ["Summit Vehicle Leasing", "Summit Leasing", "Summit Fleet", "SUMMIT VL"]),
    ("Orchard Maintenance Co", ["Orchard Maintenance Co", "Orchard Maint.", "Orchard Services", "ORCHARD"]),
]

ITEMS = [
    ("IT Hardware", "Laptops", "Business laptop 14 inch", "each", 920.0),
    ("IT Hardware", "Laptops", "Laptop dock USB-C", "each", 175.0),
    ("IT Hardware", "Displays", "Monitor 27 inch QHD", "each", 245.0),
    ("IT Hardware", "Peripherals", "Wireless keyboard and mouse set", "each", 48.0),
    ("IT Hardware", "Peripherals", "USB-C headset", "each", 82.0),
    ("Software & Cloud", "Software Licenses", "Project collaboration annual license", "license", 138.0),
    ("Software & Cloud", "Software Licenses", "PDF editor annual license", "license", 96.0),
    ("Software & Cloud", "Cloud Services", "Cloud storage monthly subscription", "month", 310.0),
    ("Software & Cloud", "Cloud Services", "Cloud compute usage", "hour", 4.8),
    ("Office & MRO", "Office Supplies", "A4 copy paper case 5 reams", "case", 31.0),
    ("Office & MRO", "Office Supplies", "Black toner cartridge", "each", 74.0),
    ("Office & MRO", "Maintenance Supplies", "Nitrile safety gloves box", "box", 18.0),
    ("Office & MRO", "Maintenance Supplies", "Industrial air filter medium", "each", 42.0),
    ("Logistics", "Freight Services", "Domestic parcel delivery", "shipment", 28.0),
    ("Logistics", "Freight Services", "Pallet freight delivery", "shipment", 185.0),
    ("Logistics", "Packaging", "Corrugated shipping box medium", "box", 1.4),
    ("Facilities & Energy", "Facilities Services", "Office cleaning monthly service", "month", 1250.0),
    ("Facilities & Energy", "Energy", "Electricity supply usage", "MWh", 132.0),
    ("Professional Services", "Consulting", "Process improvement consultant", "day", 1150.0),
    ("Professional Services", "Consulting", "Data analytics consultant", "day", 1280.0),
    ("Safety & Lab", "Personal Protective Equipment", "Protective safety glasses", "each", 12.0),
    ("Safety & Lab", "Laboratory Supplies", "Laboratory nitrile gloves box", "box", 21.0),
    ("Telecom", "Telecom Services", "Mobile voice and data plan", "month", 44.0),
    ("Telecom", "Telecom Services", "Business broadband connection", "month", 88.0),
    ("Fleet", "Vehicle Leasing", "Compact vehicle lease monthly", "month", 465.0),
    ("Fleet", "Vehicle Leasing", "Electric van lease monthly", "month", 790.0),
    ("Office & MRO", "Office Supplies", "Whiteboard marker pack 12", "pack", 14.0),
    ("IT Hardware", "Peripherals", "USB-C adapter multiport", "each", 39.0),
    ("Facilities & Energy", "Facilities Services", "HVAC preventive maintenance visit", "visit", 390.0),
    ("Logistics", "Packaging", "Stretch wrap roll clear", "roll", 19.0),
]

CURRENCIES = ["EUR", "USD", "GBP"]
CURRENCY_UNITS_PER_USD = {"USD": 1.0, "EUR": 1 / 1.08, "GBP": 1 / 1.27}
REGIONS = ["UK", "Germany", "France", "United States", "Netherlands", "Ireland"]


def _date_text(value: date, style: str) -> str:
    if style == "iso":
        return value.strftime("%Y-%m-%d")
    if style == "slash":
        return value.strftime("%d/%m/%Y")
    if style == "us":
        return value.strftime("%m-%d-%Y")
    return value.strftime("%b %d, %Y")


def _description(item: str, source: str, rng: random.Random) -> str:
    if source == "erp":
        return item
    if source == "pcard":
        prefixes = ["PURCHASE", "POS", "ONLINE ORDER", "CARD TXN"]
        return f"{rng.choice(prefixes)} - {item.upper()} / {rng.randint(100, 9999)}"
    return rng.choice([item, item.lower(), f"{item} (urgent)", f"{item} - dept order"])


def _supplier_name(canonical: str, source: str, rng: random.Random) -> str:
    aliases = next(values for name, values in SUPPLIERS if name == canonical)
    if source == "erp":
        return canonical if rng.random() < 0.8 else rng.choice(aliases)
    value = rng.choice(aliases)
    if source == "pcard" and rng.random() < 0.4:
        value = f"{value} #{rng.randint(10, 99)}"
    if source == "manual" and rng.random() < 0.25:
        value = value.replace(" ", "")
    return value


def _make_row(source: str, row_number: int, rng: random.Random) -> dict[str, object]:
    family, subcategory, item, unit, base_price = rng.choice(ITEMS)
    canonical_supplier = rng.choice(SUPPLIERS)[0]
    currency = rng.choice(CURRENCIES)
    source_price_bias = {"erp": 1.0, "pcard": 1.12, "manual": 0.91}[source]
    price = round(base_price * source_price_bias * rng.uniform(0.94, 1.06) * CURRENCY_UNITS_PER_USD[currency], 2)
    if item == "Business laptop 14 inch" and source == "manual":
        price = round(price * 1.55, 2)
    transaction_date = date(2025, 1, 1) + timedelta(days=rng.randrange(365))
    source_category = f"{family} > {subcategory}"
    if rng.random() < {"erp": 0.04, "pcard": 0.38, "manual": 0.24}[source]:
        source_category = ""
    elif source == "pcard":
        source_category = rng.choice([family.upper(), subcategory, f"{family}/{subcategory}", source_category])
    elif source == "manual":
        source_category = rng.choice([source_category, subcategory.lower(), f"{family} - {subcategory}"])
    date_style = {"erp": "iso", "pcard": "us", "manual": "slash"}[source]
    if source == "manual" and rng.random() < 0.2:
        date_style = "text"
    unit_value = unit
    if source == "manual":
        unit_value = {"each": "ea", "box": "BOXES", "month": "mo", "license": "licence"}.get(unit, unit)
    return {
        "source_system": {"erp": "ERP", "pcard": "PCARD", "manual": "MANUAL"}[source],
        "source_transaction_id": f"{source.upper()}-{row_number:05d}",
        "invoice_date": _date_text(transaction_date, date_style),
        "supplier_name": _supplier_name(canonical_supplier, source, rng),
        "description": _description(item, source, rng),
        "category": source_category,
        "quantity": rng.choice([1, 1, 1, 2, 3, 5, 10]),
        "unit": unit_value,
        "unit_price": price,
        "currency": currency,
        "region": rng.choice(REGIONS),
    }


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def generate() -> None:
    rng = random.Random(SEED)
    source_specs = [("erp", 500), ("pcard", 400), ("manual", 300)]
    fields = [
        "source_system", "source_transaction_id", "invoice_date", "supplier_name",
        "description", "category", "quantity", "unit", "unit_price", "currency", "region",
    ]
    for source, count in source_specs:
        rows = [_make_row(source, index + 1, rng) for index in range(count)]
        duplicate_count = {"erp": 10, "pcard": 12, "manual": 9}[source]
        rows.extend(dict(row) for row in rows[:duplicate_count])
        _write_csv(RAW_DIR / f"{source}_export.csv", rows, fields)

    supplier_rows = [
        {"canonical_supplier": canonical, "known_aliases": " | ".join(aliases)}
        for canonical, aliases in SUPPLIERS
    ]
    _write_csv(REFERENCE_DIR / "supplier_master.csv", supplier_rows, ["canonical_supplier", "known_aliases"])

    taxonomy_rows = [
        {"category_level_1": family, "category_level_2": subcategory}
        for family, subcategory in sorted({(family, subcategory) for family, subcategory, *_ in ITEMS})
    ]
    _write_csv(REFERENCE_DIR / "category_taxonomy.csv", taxonomy_rows, ["category_level_1", "category_level_2"])


if __name__ == "__main__":
    generate()