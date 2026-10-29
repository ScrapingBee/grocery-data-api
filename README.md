# Grocery Data API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="REPLACE_WITH_SCREENSHOT_URL" alt="grocery-data-api" />
  </a>
</p>

[![checks](https://github.com/ScrapingBee/grocery-data-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/grocery-data-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/grocery-data-api.svg)](LICENSE)

Shelf prices do not compare. A 2.272 litre jug, a 1 gallon jug and a 6 x 1 litre pack of UHT milk only line up once each is turned into a price per litre, and every store prints that number differently. This repo is a small [grocery api](https://www.scrapingbee.com/scrapers/grocery-data-api/) that does the lining up: three per-store adapters (Tesco, ALDI UK and Walmart) feed one normalised product schema, and a matrix script puts the cheapest price per litre, kilogram or egg side by side. All of it runs on [ScrapingBee](https://www.scrapingbee.com/), and every number below comes from live runs on 2026-10-29.

## The matrix

`python examples/grocery_matrix.py --zip 10001 --usd-gbp 0.75` on 2026-10-29, 36 credits:

```
            tesco                             aldi                              walmart                           walmart in GBP
milk        £0.77/l (med £1.50, n=19)         £0.73/l (med £0.95, n=4)          $1.12/l (med $3.14, n=23)         £0.84/l
flour       £0.47/kg (med £1.10, n=7)         £0.47/kg (med £0.47, n=1)         $0.87/kg (med $2.95, n=32)        £0.65/kg
eggs        £0.19/each (med £0.41, n=23)      £0.17/each (med £0.25, n=9)       $0.14/each (med $0.27, n=24)      £0.10/each

  milk   tesco    cheapest: Tesco British Whole Milk 2.272L, 4 Pints  [£0.77/litre]
  milk   aldi     cheapest: Whole Milk 3.6% Fat  [£0.73/1 L]
  milk   walmart  cheapest: Great Value Whole Vitamin D Milk, Gallon  [$1.06/qt]
  flour  tesco    cheapest: Stockwell & Co. Plain Flour 1.5Kg  [£0.47/kg]
  flour  aldi     cheapest: Plain Flour  [£0.47/1 KG]
  flour  walmart  cheapest: Great Value All-Purpose Enriched Bleached Wheat Flour, 5 lb   [39.4 ¢/lb]
  eggs   tesco    cheapest: Tesco Mixed Sized Free Range Eggs 15 Pack  [£3.54/kg]
  eggs   aldi     cheapest: Free Range Eggs 6 Pack  [£0.17/1 Each]
  eggs   walmart  cheapest: Great Value Large White Eggs, 60 Count  [$1.08/lb]

credits: {'tesco': 3, 'aldi': 3, 'walmart': 30} = 36
walmart store used: {'city': 'Secaucus', 'state': 'NJ', 'store_id': '3520', 'zipcode': '07094'}
```

How to read it:

- **Each cell** is the lowest normalised unit price among the relevant products on page 1 of that store's search, then the median and how many products qualified. `n` is small for ALDI because its search returns about 30 mixed results and few are plain flour.
- **The label in brackets** is what the store printed. Tesco says `£0.77/litre`, ALDI says `£0.73/1 L`, Walmart says `$1.06/qt` and `39.4 ¢/lb`. The adapters convert all of them to per litre, per kg or per item.
- **Eggs are the interesting row.** Walmart prints egg unit prices per pound (`$1.08/lb`), and Tesco did the same for its 15 pack (`£3.54/kg`). An egg is compared per egg, so `rebase()` in the matrix script falls back to the count in the product name (`60 Count`, `15 Pack`) and recomputes from the shelf price. Those rows are marked `unit_source="title"`.
- **Currencies are not mixed.** GBP and USD stay in their own columns. The last column exists only because `--usd-gbp 0.75` was passed on the command line; the script holds no exchange rate of its own, and 0.75 here is an example input, not a quoted rate.
- **Walmart is localised by store.** `--zip 10001` resolved to store 3520 in Secaucus, NJ. With no ZIP, the same query was answered from store 375 in Huntsville, AL. Prices can differ by store, so record `location` with every pull.

## The normalised product schema

Every adapter returns this dataclass, whatever the store's own format:

```python
@dataclass
class GroceryProduct:
    store: str              # tesco | aldi | walmart
    country: str            # GB | US
    product_id: str         # Tesco tpnc, ALDI sku, Walmart item id
    name: str
    brand: str | None
    price: float            # shelf price in `currency`
    currency: str           # GBP | USD
    unit_price: float | None        # price per `unit`, in `currency`
    unit: str | None                # "l", "kg" or "each" after normalising
    unit_price_label: str | None    # what the store printed
    unit_source: str                # "store" or "title"
    available: bool | None
    promo: str | None
    url: str | None
    gtin: str | None = None
```

Unit conversion is one table. A store's "price per X" is divided by the quantity and multiplied by how many X fit in the base unit:

```python
PER_UNIT = {
    "l": ("l", 1.0), "litre": ("l", 1.0), "ml": ("l", 1000.0), "cl": ("l", 100.0),
    "fl oz": ("l", 33.814), "gal": ("l", 1 / 3.78541), "qt": ("l", 1 / 0.946353),
    "kg": ("kg", 1.0), "g": ("kg", 1000.0), "oz": ("kg", 1 / 0.0283495), "lb": ("kg", 1 / 0.453592),
    "each": ("each", 1.0), "ea": ("each", 1.0), "ct": ("each", 1.0), "count": ("each", 1.0),
}
```

So Walmart's `9.3 ¢/fl oz` becomes 0.093 x 33.814 = $3.14 per litre, and `39.4 ¢/lb` becomes 0.394 / 0.453592 = $0.87 per kg. One trap: Walmart uses `oz` for weight and `fl oz` for volume, but some milk titles say `96 oz`. When the store printed a unit price the adapter uses it (`6.6 ¢/fl oz` for that Lactaid jug), and only falls back to parsing the title when the store gave nothing.

Across the 253 products the run collected, the unit price came from the store's own label for 196 and from the product name for 51; 6 Walmart products had neither and are excluded from the matrix.

## Grocery data scraping, store by store

### Tesco: the Apollo cache in `discover+json`

`https://www.tesco.com/groceries/en-GB/search?query=...` now answers with a 301 to `https://www.tesco.com/shop/en-GB/search?query=...`. The new URL server-renders a `<script type="application/discover+json">` block, and inside it, at `mfe-orchestrator.props.apolloCache`, the GraphQL cache of the search. Classic proxy, no JavaScript, 1 credit. (`mode=auto` on the old URL settled on 5 credits, with JavaScript on; the new URL without JavaScript returned the same cache for 1.)

One product from that cache, trimmed:

```json
"ProductType:287971983": {
  "__typename": "ProductType",
  "id": "287971983",
  "tpnb": "78838998",
  "gtin": "05000181035179",
  "title": "Arla Big Milk Fresh Whole Milk  2L Vitamin Enriched for kids 1+ ",
  "brandName": "Arla",
  "superDepartmentName": "Groceries & Essentials",
  "departmentName": "Fresh Food",
  "aisleName": "Milk, Butter & Eggs",
  "shelfName": "Milk",
  "status": "AvailableForSale",
  "price": { "__typename": "PriceType", "actual": 2.75, "unitPrice": 1.38, "unitOfMeasure": "litre" },
  "promotions": [{ "__typename": "PromotionType", "id": "100144020", "attributes": ["CLUBCARD_PRICING"],
                   "__ref": "PromotionType:{\"id\":\"100144020\",\"description\":\"Any 2 for £4 Clubcard Price - Selected Dairy Products 2 Litre\"}" }],
  "reviews": { "stats": { "noOfReviews": 135, "overallRating": 4.7, "overallRatingRange": 5 } }
}
```

The cache is keyed by type and id, so the order on the page comes from `ROOT_QUERY`, whose key is the GraphQL call with its arguments:

```
search({"appliedFacetArgs":[],...,"journey":"ONLINE","mode":"DEFAULT","page":1,"query":"whole milk","sortBy":"relevance"})
  -> {"info": {"total": 27, "page": 1, "count": 24, "pageSize": 24, "offset": 0, ...},
      "results": [{"node": {"__ref": "ProductType:287971983"}}, ...]}
```

Notes from the data: Tesco gives the unit price itself, so no size parsing is needed; `gtin` is the barcode, which is the only field here that can join a product across retailers; one `unitOfMeasure` read `litre DR.WT` and the adapter keeps only the first word; the Clubcard promotion text lives inside the `__ref` string, not as a field. Pagination is `&page=2&count=24`, the same link Tesco's own pager uses.

### ALDI UK: the Nuxt payload in `__NUXT_DATA__`

`https://www.aldi.co.uk/results?q=whole+milk` is a Nuxt page. `mode=auto` settled on the classic tier without JavaScript, 1 credit. The data is in `<script type="application/json" id="__NUXT_DATA__">`, which is not ordinary JSON objects but Nuxt's flattened format: one big array where every value is an index into the same array. The raw product record:

```json
{"sku": 8757, "name": 8758, "brandName": 8648, "urlSlugText": 8759, "quantityUnit": 8762,
 "sellingSize": 8763, "price": 8765, "categories": "...", "notForSale": 1186, ...}
```

`devalue()` in the adapter follows the indices (and unwraps `["Reactive", n]` wrappers) to get:

```json
{
  "sku": "000000000000416772",
  "name": "Whole Milk 3.6% Fat",
  "brandName": "COWBELLE",
  "urlSlugText": "cowbelle-whole-milk-3-6-fat",
  "sellingSize": "2.272 l",
  "notForSale": true,
  "price": {
    "amount": 165, "amountRelevantDisplay": "£1.65",
    "comparison": 73, "comparisonDisplay": "£0.73/1 L",
    "currencyCode": "GBP", "currencySymbol": "£", "wasPriceDisplay": null
  },
  "categories": [{ "name": "Chilled Food" }, { "name": "Milk", "urlSlugText": "chilled-food/milk" }]
}
```

Prices are integers in pence (`165`), and `comparison` is the unit price in pence per the quantity in `comparisonDisplay`, which can be `1 L`, `1 KG`, `1 Each` or `1 Pack`. `1 Pack` is not a unit, so those products fall back to the size in the name. The same values are in the HTML as `data-test` attributes (`product-tile__name`, `product-tile__comparison-price`, `product-tile__price`) if you prefer CSS selectors. Pagination is `&page=N`, 30 per page; the search meta read `{"offset": 0, "limit": 30, "totalCount": 104}` for whole milk.

Every one of the 72 ALDI products in the run came back with `notForSale: true`, so the adapter reports `available=False` for all of them. The site shows these prices but flags none of them as sold online; treat ALDI rows as shelf price data, not as stock data.

### Walmart: ScrapingBee's dedicated endpoint, plus `__NEXT_DATA__` for unit prices

Walmart goes through ScrapingBee's [Walmart Search API](https://www.scrapingbee.com/documentation/walmart/) rather than the HTML API, 10 credits per call:

```bash
curl "https://app.scrapingbee.com/api/v1/walmart/search?query=whole%20milk&delivery_zip=10001&add_html=true" \
  -H "Authorization: Bearer $SCRAPINGBEE_API_KEY"
```

The live response did not match the example on the documentation page. The docs show `meta_data`, `position`, `product_id` and `review_count`; what came back on 2026-10-29 had top level keys `html, location, page, products, products_count, screenshot, url`, and each product looked like this:

```json
{"currency": "USD", "fulfillment": {"delivery": false, "free_shipping": false, "pickup": false, "shipping": false},
 "id": "10450114", "image": "https://i5.walmartimages.com/seo/Great-Value-Whole-Vitamin-D-Milk-Gallon-Plastic-Jug-128-Fl-Oz_....jpeg",
 "out_of_stock": false, "pos": 2, "price": 4.25, "rating": 4.6, "rating_count": 430424,
 "section_title": "Results for \"whole milk gallon\"", "seller_id": "F55CDC31AB754BB68FE0B39041159D63",
 "seller_name": "Walmart.com", "sponsored": false, "title": "Great Value Whole Vitamin D Milk, Gallon",
 "url": "/ip/Great-Value-Whole-Vitamin-D-Milk-Gallon-Plastic-Jug-128-Fl-Oz/10450114?athAsset=..."}
```

The parsed products have no unit price. `add_html=true` adds the page HTML at no extra cost (10 credits either way in our calls), and its `__NEXT_DATA__` has one, in a place that is easy to miss. For the main results, `priceInfo.unitPrice` is an empty string and the value sits one level down:

```json
"priceInfo": {
  "linePrice": "", "unitPrice": "",
  "priceDetails": {
    "currency": "USD",
    "priceLines": [
      { "lineType": "CURRENT_PRICE", "values": [{ "key": "PRICE", "value": "5.97" }] },
      { "lineType": "UNIT_PRICE", "values": [{ "key": "UNIT_PRICE", "value": "9.3 ¢/fl oz" }] }
    ]
  }
}
```

The carousel modules elsewhere on the page do fill `priceInfo.unitPrice` directly (`"2.5 ¢/fl oz"`, `"$2.57/lb"`). `walmart_unit_prices()` reads both shapes and joins them to the API products by `usItemId`. Two more details: `pos` restarts at 1 when the page switches from one result section to the next, and sponsored slots repeat organic items, so the adapter de-duplicates on `id`.

## Run it

```bash
export SCRAPINGBEE_API_KEY=your_key
pip install requests
cd examples
python grocery_matrix.py --zip 10001 --json basket.json --csv products.csv
python grocery_matrix.py --items milk --stores tesco aldi          # 2 credits
```

The basket is data, not code. Each line names the unit, the search phrase per store, words a name must contain and words that disqualify it:

```python
"eggs": BasketLine(
    "eggs", "each",
    {"tesco": "free range eggs", "aldi": "free range eggs", "walmart": "large eggs"},
    must=["eggs"],
    never=["chocolate", "noodle", "liquid", "whites", "cooker", "easter", "mayo", "pasta", "boiled", "bites",
           "powder", "powdered", "pickled", "cooked", "scotch", "picnic", "custard", "savoury"],
),
```

The exclusion list is there because searches are loose. ALDI's results for `free range eggs` included `Egg Custard Tarts`, `Scotch Eggs 2 Pack` and `Plain Toasting Egg Waffles`, and on our first run the waffles won the eggs row at £0.13 each. Walmart's `whole milk` results included milk powder at $23.98/kg. Check the `relevant` column in the CSV before trusting a cell.

## Node.js: one store, sorted by unit price

[`examples/tesco_unit_prices.js`](examples/tesco_unit_prices.js) reads the same Tesco cache with Node 18 and no dependencies:

```bash
node examples/tesco_unit_prices.js "plain flour"
```

```
£0.7	£0.47/kg	Stockwell & Co. Plain Flour 1.5Kg	[Food Cupboard > Home Baking & Sugar > Flour]
£0.8	£0.53/kg	Tesco Plain Flour 1.5Kg	[Food Cupboard > Home Baking & Sugar > Flour]
£3.25	£1.08/kg	Allinson's Plain White Flour 3kg	[Food Cupboard > Home Baking & Sugar > Flour]
£1.1	£1.1/kg	Carr's Premium Plain Flour 1Kg	[Food Cupboard > Home Baking & Sugar > Flour]
page 1: 24 of 2164 results for "plain flour", 1 credit(s)
```

## What was tried and dropped

Coles (`coles.com.au/search/products?q=...`) returned a 211 byte body through `mode=auto` at 5 credits, with no products, so Australia is not in the matrix. We did not test Sainsbury's, Lidl or Ocado for this repo. Adding a store means writing one class with a `search(query) -> list[GroceryProduct]` method and registering it in `ADAPTERS`.

## Credit cost per store

| Store | Request | Credits per search page |
|---|---|---|
| Tesco | HTML API, classic proxy, `render_js=false` | 1 |
| ALDI UK | HTML API, classic proxy, `render_js=false` | 1 |
| Walmart | Walmart Search API, `add_html=true` | 10 |

A three item basket across all three stores is 36 credits. The 1,000 free credits on a new account cover about 27 full runs, or 1,000 Tesco or ALDI searches if you drop Walmart. Failed calls that ScrapingBee answers with HTTP 500 are not billed, and both adapters and the Node script retry them.

## Related ScrapingBee scrapers

[Walmart price API](https://www.scrapingbee.com/scrapers/walmart-price-api/), [Tesco API](https://www.scrapingbee.com/scrapers/tesco-api/), [ALDI API](https://www.scrapingbee.com/scrapers/aldi-api/), [UK supermarket API](https://www.scrapingbee.com/scrapers/uk-supermarket-api/), [Sainsbury's API](https://www.scrapingbee.com/scrapers/sainsburys-api/), [Ocado API](https://www.scrapingbee.com/scrapers/ocado-api/), [food data API](https://www.scrapingbee.com/scrapers/food-data-api/).

Documentation: [HTML API and credit costs](https://www.scrapingbee.com/documentation/), [Walmart API reference](https://www.scrapingbee.com/documentation/walmart/), [CSS extraction rules](https://www.scrapingbee.com/documentation/data-extraction/), [Walmart scraper feature page](https://www.scrapingbee.com/features/walmart/), [pricing](https://www.scrapingbee.com/pricing/).

## Scope

Public search pages, read without an account. Basket contents, delivery slots, loyalty prices tied to a signed in Clubcard and order history are outside it; ScrapingBee does not allow scraping behind a login, and none of these adapters sends credentials or session cookies. Tesco shows Clubcard promotions to everyone as text, and that text is captured as `promo`; the Clubcard price itself is not in the cache we read. Store terms apply to each site.

## FAQ

**Which stores does this grocery API cover?**
Tesco and ALDI in the UK and Walmart in the US, each verified live on 2026-10-29. Coles in Australia returned an empty body and was left out. ScrapingBee's 1,000 free credits are enough to try all three.

**Where do I get price per unit grocery data instead of shelf prices?**
Tesco has it as `price.unitPrice` with `unitOfMeasure`, ALDI as `price.comparison` with `comparisonDisplay`, and Walmart in `priceInfo.priceDetails.priceLines` inside the page's `__NEXT_DATA__`. The adapters convert all three to per litre, per kg or per item.

**Can I compare UK and US grocery prices directly?**
Only after you choose an exchange rate. Pass `--usd-gbp <rate>` and the matrix adds a converted Walmart column; otherwise each store stays in its own currency.

**Does grocery data scraping need JavaScript rendering?**
Not for these three. Tesco and ALDI server-render their data and cost 1 credit without JavaScript; Walmart goes through the dedicated endpoint.

**How do I get Walmart prices for a specific store?**
Pass `delivery_zip` (or `store_id`) to the Walmart Search API and keep the returned `location`. Without either, our request was priced at a store in Huntsville, AL.

## License

MIT. See [LICENSE](LICENSE).
