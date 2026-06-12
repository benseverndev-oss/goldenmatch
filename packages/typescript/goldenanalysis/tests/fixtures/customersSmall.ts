// 20-row fixture matching the Python `build_customers_small()` data
// (packages/python/goldenanalysis/tests/fixtures/__init__.py). Engineered so every
// frame.summary metric is hand-verifiable: rows 0 & 1 are a full-row duplicate
// (duplicate_row_ratio = 2/20 = 0.1); null counts name 4 / email 6 / city 2 / age 10
// (null_ratio_mean = (0.2+0.3+0.1+0.5)/4 = 0.275).

const NAMES = [
  "Alice", "Alice", "Bob", "Carol", null, "Dave", "Eve", null, "Frank", "Grace",
  "Heidi", null, "Ivan", "Judy", "Karl", null, "Liam", "Mona", "Nina", "Omar",
];
const EMAILS = [
  "alice@x.com", "alice@x.com", null, "carol@x.com", "e@x.com", null, "eve@x.com",
  "g@x.com", null, "grace@x.com", null, "h@x.com", "ivan@x.com", null, "karl@x.com",
  "l@x.com", null, "mona@x.com", "nina@x.com", "omar@x.com",
];
const CITIES = [
  "NYC", "NYC", "LA", "SF", "SF", "Chicago", "Boston", "Miami", "Denver", null,
  "Seattle", "Austin", "Portland", "Reno", "Tucson", "Mesa", "Provo", "Ogden", null, "Boise",
];
const AGES = [
  30, 30, null, null, null, 40, null, 35, null, 28,
  null, 33, null, 45, null, 22, null, 38, null, 50,
];

export function buildCustomersSmall(): Array<Record<string, unknown>> {
  return NAMES.map((name, i) => ({
    name,
    email: EMAILS[i],
    city: CITIES[i],
    age: AGES[i],
  }));
}
