// Tesco search -> products sorted by unit price. Node 18+, no dependencies.
//
//   SCRAPINGBEE_API_KEY=... node tesco_unit_prices.js "plain flour" [page]
//
// 1 credit: classic proxy, render_js=false. The search page ships its Apollo
// cache in <script type="application/discover+json">, so no rendering is
// needed to read prices.

const KEY = process.env.SCRAPINGBEE_API_KEY;
if (!KEY) {
  console.error("Set SCRAPINGBEE_API_KEY first.");
  process.exit(1);
}
const query = process.argv[2] || "whole milk";
const page = process.argv[3] || "1";

async function get(url, tries = 3) {
  for (let i = 1; i <= tries; i++) {
    const qs = new URLSearchParams({ url, render_js: "false" });
    const res = await fetch(`https://app.scrapingbee.com/api/v1/?${qs}`, {
      headers: { Authorization: `Bearer ${KEY}` },
    });
    if (res.status === 500 && i < tries) continue; // unbilled, retry
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { html: await res.text(), cost: res.headers.get("spb-cost") };
  }
}

(async () => {
  const url = `https://www.tesco.com/shop/en-GB/search?query=${encodeURIComponent(query)}&page=${page}&count=24`;
  const { html, cost } = await get(url);
  const m = html.match(/<script type="application\/discover\+json">([\s\S]*?)<\/script>/);
  if (!m) throw new Error("no discover+json block, Tesco changed the page");
  const cache = JSON.parse(m[1])["mfe-orchestrator"].props.apolloCache;

  const searchKey = Object.keys(cache.ROOT_QUERY).find((k) => k.startsWith("search(") && k.includes('"mode":"DEFAULT"'));
  const { info, results } = cache.ROOT_QUERY[searchKey];
  const rows = results
    .map((r) => cache[r.node.__ref])
    .filter(Boolean)
    .map((p) => ({
      id: p.id,
      gtin: p.gtin,
      title: p.title.trim(),
      price: p.price?.actual,
      unit: p.price?.unitPrice != null ? `${p.price.unitPrice}/${p.price.unitOfMeasure}` : null,
      unitValue: p.price?.unitPrice ?? Infinity,
      shelf: `${p.departmentName} > ${p.aisleName} > ${p.shelfName}`,
      rating: p.reviews?.stats?.overallRating ?? null,
    }))
    .sort((a, b) => a.unitValue - b.unitValue);

  for (const r of rows.slice(0, 8)) {
    console.log(`£${r.price}\t£${r.unit}\t${r.title}\t[${r.shelf}]`);
  }
  console.log(`page ${info.page}: ${info.count} of ${info.total} results for "${info.query.actualTerm}", ${cost} credit(s)`);
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
