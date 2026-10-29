"""Per-store grocery adapters behind one normalised product schema.

Three stores, three different data sources, one output record:

    Tesco (UK)    tesco.com search page, Apollo cache in a
                  <script type="application/discover+json"> block.  1 credit.
    ALDI (UK)     aldi.co.uk results page, Nuxt payload in
                  <script id="__NUXT_DATA__">.                     1 credit.
    Walmart (US)  ScrapingBee Walmart Search API with add_html=true;
                  products from the JSON, unit price from the
                  page's __NEXT_DATA__.                            10 credits.

Every adapter returns GroceryProduct records with the shelf unit price
converted to a common basis: per litre, per kilogram or per item.

Verified live on 2026-10-29. Set SCRAPINGBEE_API_KEY before use.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

API = "https://app.scrapingbee.com/api/v1/"

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


@dataclass
class GroceryProduct:
    store: str              # tesco | aldi | walmart
    country: str            # GB | US
    product_id: str         # store's own id (Tesco tpnc, ALDI sku, Walmart item id)
    name: str
    brand: str | None
    price: float            # shelf price in `currency`
    currency: str           # GBP | USD
    unit_price: float | None        # price per `unit`, in `currency`
    unit: str | None                # "l", "kg" or "each" after normalising
    unit_price_label: str | None    # what the store printed, e.g. "£0.73/1 L"
    unit_source: str                # "store" or "title" (parsed from the name)
    available: bool | None
    promo: str | None
    url: str | None
    gtin: str | None = None


# --------------------------------------------------------------------------
# Unit normalisation
# --------------------------------------------------------------------------

# Factor that converts "price per <unit>" into "price per <base>".
# e.g. 2.5 cents per US fl oz * 33.814 = 84.5 cents per litre.
PER_UNIT = {
    "l": ("l", 1.0), "litre": ("l", 1.0), "liter": ("l", 1.0), "ltr": ("l", 1.0),
    "ml": ("l", 1000.0), "cl": ("l", 100.0),
    "fl oz": ("l", 33.814), "floz": ("l", 33.814),
    "gal": ("l", 1 / 3.78541), "gallon": ("l", 1 / 3.78541),
    "qt": ("l", 1 / 0.946353), "pt": ("l", 1 / 0.473176),
    "kg": ("kg", 1.0), "g": ("kg", 1000.0),
    "oz": ("kg", 1 / 0.0283495), "lb": ("kg", 1 / 0.453592),
    "each": ("each", 1.0), "ea": ("each", 1.0), "ct": ("each", 1.0), "count": ("each", 1.0),
}


def per_base(price_per: float, qty: float, unit: str) -> tuple[float, str] | None:
    """Price for `qty` `unit` -> price per base unit (l, kg, each)."""
    u = unit.lower().strip().rstrip(".")
    if u not in PER_UNIT or qty <= 0:
        return None
    base, factor = PER_UNIT[u]
    return round(price_per / qty * factor, 4), base


# Pack sizes written in product names: "2.272L", "568Ml", "64 fl oz",
# "1 Gallon", "1.5kg", "12 Count", "6 X 1 Litre".
SIZE = re.compile(
    r"(?:(\d+)\s*[xX]\s*)?(\d+(?:\.\d+)?)\s*(fl\.?\s?oz|fluid\s?ounces?|ml|cl|litres?|liters?|ltr|l|kg|g|oz|lb|gallon|gal|count|ct|pack)\b",
    re.I,
)
WORD_SIZE = {"gallon": (1, "gallon"), "half gallon": (0.5, "gallon"), "pint": (0.568261, "l"), "pints": (0.568261, "l")}


def pack_size(name: str) -> tuple[float, str] | None:
    m = SIZE.search(name)
    if m:
        multi = float(m.group(1) or 1)
        unit = m.group(3).lower().replace(".", "").replace(" ", "")
        unit = {"floz": "fl oz", "fluidounce": "fl oz", "fluidounces": "fl oz", "pack": "each",
                "litres": "l", "liters": "l"}.get(unit, unit)
        return multi * float(m.group(2)), unit
    low = name.lower()
    for word in ("half gallon", "gallon"):
        if word in low:
            return WORD_SIZE[word]
    return None


def unit_from_title(price: float, name: str) -> tuple[float, str] | None:
    size = pack_size(name)
    return per_base(price, *size) if size else None


# --------------------------------------------------------------------------
# ScrapingBee calls
# --------------------------------------------------------------------------


def _headers() -> dict:
    key = os.environ.get("SCRAPINGBEE_API_KEY") or sys.exit("Set SCRAPINGBEE_API_KEY first.")
    return {"Authorization": f"Bearer {key}"}


class Ledger:
    """Counts credits from the spb-cost header across adapters."""

    def __init__(self) -> None:
        self.by_store: dict[str, int] = {}

    def add(self, store: str, resp: requests.Response) -> None:
        self.by_store[store] = self.by_store.get(store, 0) + int(resp.headers.get("spb-cost") or 0)

    @property
    def total(self) -> int:
        return sum(self.by_store.values())


def call(endpoint: str, params: dict, retries: int = 3) -> requests.Response:
    """GET with free retries on ScrapingBee's HTTP 500 (failed calls are not billed)."""
    for attempt in range(1, retries + 1):
        r = requests.get(endpoint, headers=_headers(), params=params, timeout=180)
        if r.status_code == 500 and attempt < retries:
            time.sleep(2 * attempt)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")


def html_page(url: str) -> requests.Response:
    # Both UK pages server-render their data, so no JavaScript: 1 credit.
    return call(API, {"url": url, "render_js": "false"})


# --------------------------------------------------------------------------
# Tesco
# --------------------------------------------------------------------------

DISCOVER = re.compile(r'<script type="application/discover\+json">(.*?)</script>', re.S)


class Tesco:
    name, country = "tesco", "GB"

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def search(self, query: str, page: int = 1) -> list[GroceryProduct]:
        url = f"https://www.tesco.com/shop/en-GB/search?query={quote_plus(query)}&page={page}&count=24"
        r = html_page(url)
        self.ledger.add(self.name, r)
        m = DISCOVER.search(r.text)
        if not m:
            raise ValueError("Tesco page without the discover+json block")
        cache = json.loads(m.group(1))["mfe-orchestrator"]["props"]["apolloCache"]
        # ROOT_QUERY holds the search call with its arguments as the key; its
        # results list gives the on-page order as references into the cache.
        key = next(k for k in cache["ROOT_QUERY"] if k.startswith("search(") and '"mode":"DEFAULT"' in k)
        refs = [res["node"]["__ref"] for res in cache["ROOT_QUERY"][key]["results"]]
        return [self._row(cache[ref]) for ref in refs if ref in cache]

    def _row(self, p: dict) -> GroceryProduct:
        price = p.get("price") or {}
        uom = (price.get("unitOfMeasure") or "").split(" ")[0]   # "litre DR.WT" -> "litre"
        qty_m = re.match(r"(\d+(?:\.\d+)?)\s*(\w+)", uom)
        norm = per_base(price["unitPrice"], float(qty_m.group(1)), qty_m.group(2)) if qty_m and price.get("unitPrice") else (
            per_base(price["unitPrice"], 1, uom) if price.get("unitPrice") else None)
        promos = [json.loads(x["__ref"].split(":", 1)[1]).get("description") for x in p.get("promotions") or [] if "__ref" in x]
        return GroceryProduct(
            store=self.name, country=self.country, product_id=p["id"], name=p["title"].strip(),
            brand=p.get("brandName"), price=price.get("actual"), currency="GBP",
            unit_price=norm[0] if norm else None, unit=norm[1] if norm else None,
            unit_price_label=f"£{price.get('unitPrice')}/{price.get('unitOfMeasure')}" if price.get("unitPrice") else None,
            unit_source="store", available=p.get("status") == "AvailableForSale",
            promo="; ".join(x for x in promos if x) or None,
            url=f"https://www.tesco.com/shop/en-GB/products/{p['id']}", gtin=p.get("gtin"),
        )


# --------------------------------------------------------------------------
# ALDI UK
# --------------------------------------------------------------------------

NUXT = re.compile(r'<script type="application/json"[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def devalue(payload: list, index: int, depth: int = 0):
    """Resolve Nuxt's flattened payload: every value is an index into the array."""
    v = payload[index]
    if depth > 12:
        return None
    if isinstance(v, dict):
        return {k: devalue(payload, i, depth + 1) for k, i in v.items()}
    if isinstance(v, list):
        if v and isinstance(v[0], str) and v[0] in ("Reactive", "ShallowReactive", "Ref", "ShallowRef"):
            return devalue(payload, v[1], depth)
        return [devalue(payload, i, depth + 1) for i in v]
    return v


class Aldi:
    name, country = "aldi", "GB"

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def search(self, query: str, page: int = 1) -> list[GroceryProduct]:
        url = f"https://www.aldi.co.uk/results?q={quote_plus(query)}" + (f"&page={page}" if page > 1 else "")
        r = html_page(url)
        self.ledger.add(self.name, r)
        m = NUXT.search(r.text)
        if not m:
            raise ValueError("ALDI page without __NUXT_DATA__")
        payload = json.loads(m.group(1))
        rows, seen = [], set()
        for i, v in enumerate(payload):
            if isinstance(v, dict) and {"sku", "name", "price", "sellingSize"} <= v.keys():
                p = devalue(payload, i)
                if p["sku"] in seen or not isinstance(p.get("price"), dict):
                    continue
                seen.add(p["sku"])
                rows.append(self._row(p))
        return rows

    def _row(self, p: dict) -> GroceryProduct:
        pr = p["price"]
        label = pr.get("comparisonDisplay")                       # "£0.73/1 L"
        norm = None
        m = re.search(r"/\s*(\d+(?:\.\d+)?)?\s*([A-Za-z ]+)$", label or "")
        if m and pr.get("comparison"):
            norm = per_base(pr["comparison"] / 100, float(m.group(1) or 1), m.group(2))
        source = "store"
        if not norm:
            norm = unit_from_title(pr["amount"] / 100, f"{p['name']} {p.get('sellingSize') or ''}")
            source = "title"
        slug = p.get("urlSlugText")
        return GroceryProduct(
            store=self.name, country=self.country, product_id=p["sku"], name=p["name"],
            brand=p.get("brandName"), price=pr["amount"] / 100, currency=pr.get("currencyCode") or "GBP",
            unit_price=norm[0] if norm else None, unit=norm[1] if norm else None,
            unit_price_label=label, unit_source=source,
            available=not p.get("notForSale") if p.get("notForSale") is not None else None,
            promo=pr.get("wasPriceDisplay"),
            url=f"https://www.aldi.co.uk/product/{slug}-{p['sku']}" if slug else None,
        )


# --------------------------------------------------------------------------
# Walmart (dedicated ScrapingBee endpoint)
# --------------------------------------------------------------------------

NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
WM_UNIT = re.compile(r"(\$|)\s*([\d.]+)\s*(¢|)\s*/\s*(.+)")


def _unit_label(price_info: dict) -> str | None:
    # Main search results leave priceInfo.unitPrice empty and carry the
    # value in priceInfo.priceDetails.priceLines[lineType=UNIT_PRICE].
    # Carousel modules fill priceInfo.unitPrice directly. Read both.
    if price_info.get("unitPrice"):
        return price_info["unitPrice"]
    for line in (price_info.get("priceDetails") or {}).get("priceLines") or []:
        if line.get("lineType") == "UNIT_PRICE":
            for v in line.get("values") or []:
                if v.get("key") == "UNIT_PRICE" and v.get("value"):
                    return v["value"]
    return None


def walmart_unit_prices(html: str) -> dict[str, str]:
    """usItemId -> unit price label ("9.3 ¢/fl oz", "$2.57/lb")."""
    m = NEXT.search(html or "")
    if not m:
        return {}
    out: dict[str, str] = {}

    def walk(o):
        if isinstance(o, dict):
            pi = o.get("priceInfo")
            label = _unit_label(pi) if isinstance(pi, dict) else None
            if o.get("usItemId") and label:
                out.setdefault(str(o["usItemId"]), label)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    return out


class Walmart:
    name, country = "walmart", "US"

    def __init__(self, ledger: Ledger, delivery_zip: str | None = None) -> None:
        self.ledger, self.zip = ledger, delivery_zip

    def search(self, query: str, page: int = 1) -> list[GroceryProduct]:
        params = {"query": query, "add_html": "true", "start_page": str(page)}
        if self.zip:
            params["delivery_zip"] = self.zip
        r = call(API + "walmart/search", params)
        self.ledger.add(self.name, r)
        data = r.json()
        self.location = data.get("location")
        units = walmart_unit_prices(data.get("html"))
        rows, seen = [], set()
        for p in data.get("products") or []:
            if p["id"] in seen:          # sponsored slots repeat organic items
                continue
            seen.add(p["id"])
            rows.append(self._row(p, units.get(str(p["id"]))))
        return rows

    def _row(self, p: dict, label: str | None) -> GroceryProduct:
        norm, source = None, "store"
        m = WM_UNIT.match(label or "")
        if m:
            amount = float(m.group(2)) / (100 if m.group(3) else 1)
            norm = per_base(amount, 1, m.group(4))
        if not norm and p.get("price"):
            norm, source = unit_from_title(p["price"], p["title"]), "title"
        return GroceryProduct(
            store=self.name, country=self.country, product_id=str(p["id"]), name=p["title"],
            brand=None, price=p.get("price"), currency=p.get("currency") or "USD",
            unit_price=norm[0] if norm else None, unit=norm[1] if norm else None,
            unit_price_label=label, unit_source=source if norm else "none",
            available=not p.get("out_of_stock"), promo="sponsored" if p.get("sponsored") else None,
            url="https://www.walmart.com" + p["url"].split("?")[0] if p.get("url") else None,
        )


ADAPTERS = {"tesco": Tesco, "aldi": Aldi, "walmart": Walmart}
