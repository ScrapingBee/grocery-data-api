"""Price-per-unit comparison matrix across Tesco, ALDI UK and Walmart.

A basket line is one product idea ("whole milk") with a store-specific
search phrase, words a product name must contain, words that disqualify it,
and the unit it should be compared in. For every store the script searches,
normalises, filters, and keeps the cheapest unit price plus the median.

Usage:
    export SCRAPINGBEE_API_KEY=...
    python grocery_matrix.py                               # default basket, all stores
    python grocery_matrix.py --items milk eggs --stores tesco aldi
    python grocery_matrix.py --zip 10001 --json basket.json --csv products.csv
    python grocery_matrix.py --usd-gbp 0.79                # add a GBP column for Walmart

Credits per basket line: Tesco 1, ALDI 1, Walmart 10.
GBP and USD are never mixed unless you pass --usd-gbp yourself; the script
has no exchange rate of its own.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field

from dataclasses import replace

from grocery_adapters import ADAPTERS, GroceryProduct, Ledger, Walmart, unit_from_title


@dataclass
class BasketLine:
    key: str
    unit: str                       # l | kg | each
    queries: dict[str, str]         # store -> search phrase
    must: list[str]
    never: list[str] = field(default_factory=list)


BASKET = {
    "milk": BasketLine(
        "whole milk", "l",
        {"tesco": "whole milk", "aldi": "whole milk", "walmart": "whole milk"},
        must=["milk"],
        never=["almond", "oat", "soy", "chocolate", "powder", "powdered", "goat", "lactose", "lactofree",
               "protein", "coconut", "condensed", "evaporated", "semi", "skim", "2%", "1%", "reduced", "dry"],
    ),
    "flour": BasketLine(
        "plain / all-purpose flour", "kg",
        {"tesco": "plain flour", "aldi": "plain flour", "walmart": "all purpose flour"},
        must=["flour"],
        never=["self raising", "self-rising", "bread", "gluten", "cake", "corn", "rice", "almond", "tortilla",
               "mix", "wholemeal", "whole wheat", "spelt", "coconut", "pizza", "batter"],
    ),
    "eggs": BasketLine(
        "eggs", "each",
        {"tesco": "free range eggs", "aldi": "free range eggs", "walmart": "large eggs"},
        must=["eggs"],
        never=["chocolate", "noodle", "liquid", "whites", "cooker", "easter", "mayo", "pasta", "boiled", "bites",
               "powder", "powdered", "pickled", "cooked", "scotch", "picnic", "custard", "savoury"],
    ),
}


def rebase(p: GroceryProduct, unit: str) -> GroceryProduct:
    """Walmart prints egg unit prices per pound ("$1.11/lb"). When the store
    unit does not match the basket unit, fall back to the pack size in the
    product name ("12 Count") and recompute from the shelf price."""
    if p.unit == unit or not p.price:
        return p
    alt = unit_from_title(p.price, p.name)
    if alt and alt[1] == unit:
        return replace(p, unit_price=alt[0], unit=alt[1], unit_source="title")
    return p


def relevant(p: GroceryProduct, line: BasketLine) -> bool:
    name = p.name.lower()
    return (
        all(w in name for w in line.must)
        and not any(w in name for w in line.never)
        and p.unit == line.unit
        and p.unit_price is not None
    )


def cell(products: list[GroceryProduct], line: BasketLine) -> dict:
    keep = [p for p in products if relevant(p, line)]
    if not keep:
        return {"n": 0, "seen": len(products)}
    best = min(keep, key=lambda p: p.unit_price)
    return {
        "n": len(keep),
        "seen": len(products),
        "currency": best.currency,
        "min": best.unit_price,
        "median": round(statistics.median(p.unit_price for p in keep), 4),
        "cheapest": best.name,
        "cheapest_price": best.price,
        "cheapest_label": best.unit_price_label,
        "cheapest_url": best.url,
    }


SYMBOL = {"GBP": "£", "USD": "$"}


def fmt(c: dict, unit: str) -> str:
    if not c.get("n"):
        return f"none of {c.get('seen', 0)}"
    s = SYMBOL.get(c["currency"], c["currency"] + " ")
    return f"{s}{c['min']:.2f}/{unit} (med {s}{c['median']:.2f}, n={c['n']})"


def main() -> None:
    ap = argparse.ArgumentParser(description="Grocery price-per-unit matrix")
    ap.add_argument("--items", nargs="+", default=list(BASKET), choices=list(BASKET))
    ap.add_argument("--stores", nargs="+", default=list(ADAPTERS), choices=list(ADAPTERS))
    ap.add_argument("--zip", help="Walmart delivery ZIP code")
    ap.add_argument("--usd-gbp", type=float, help="your own USD to GBP rate, adds a converted Walmart column")
    ap.add_argument("--json", help="write the matrix and every product to JSON")
    ap.add_argument("--csv", help="write every normalised product to CSV")
    a = ap.parse_args()

    ledger = Ledger()
    adapters = {s: (Walmart(ledger, a.zip) if s == "walmart" else ADAPTERS[s](ledger)) for s in a.stores}
    matrix: dict[str, dict[str, dict]] = {}
    every: list[dict] = []

    for item in a.items:
        line = BASKET[item]
        matrix[item] = {}
        for store, adapter in adapters.items():
            try:
                products = adapter.search(line.queries[store])
            except Exception as e:  # one store failing should not sink the matrix
                print(f"{store} / {item}: {e}", file=sys.stderr)
                matrix[item][store] = {"n": 0, "error": str(e)}
                continue
            products = [rebase(p, line.unit) for p in products]
            matrix[item][store] = cell(products, line)
            every += [{"basket_item": item, "relevant": relevant(p, line), **asdict(p)} for p in products]

    width = 34
    print(f"{'':<12}" + "".join(f"{s:<{width}}" for s in adapters) + ("walmart in GBP" if a.usd_gbp and "walmart" in adapters else ""))
    for item, row in matrix.items():
        unit = BASKET[item].unit
        line = f"{item:<12}" + "".join(f"{fmt(row[s], unit):<{width}}" for s in adapters)
        w = row.get("walmart") or {}
        if a.usd_gbp and w.get("n"):
            line += f"£{w['min'] * a.usd_gbp:.2f}/{unit}"
        print(line)
    print()
    for item, row in matrix.items():
        for s, c in row.items():
            if c.get("n"):
                print(f"  {item:<6} {s:<8} cheapest: {c['cheapest'][:60]}  [{c['cheapest_label'] or 'unit from title'}]")
    print(f"\ncredits: {ledger.by_store} = {ledger.total}")
    loc = getattr(adapters.get("walmart"), "location", None)
    if loc:
        print(f"walmart store used: {loc}")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"matrix": matrix, "products": every, "credits": ledger.by_store}, f, ensure_ascii=False, indent=2)
    if a.csv and every:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(every[0]))
            w.writeheader()
            w.writerows(every)


if __name__ == "__main__":
    main()
