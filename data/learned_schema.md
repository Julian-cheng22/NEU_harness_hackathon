# Learned schema knowledge

Discovered question-blind by `qwen3.5-9b` on 2026-09-16T21:28:40+00:00.
Dataset `1.0.0`, fingerprint `f777696b2349cd02`, taxonomy `1.0.0`.

Every claim below carries the query that proved it and the number that query returned. Re-check rather than trust.

Proposed 4 | verified 4 | rejected 0 | duplicate 0

| id | claim | evidence | class | decision |
|---|---|---:|---|---|
| C01 | status column semantic drift | 2,030 | vocabulary-collision | pending |
| C02 | plan_tier cache staleness | 26 | stale-copy | pending |
| C03 | usage_events event_value text storage | 1,490 | type-drift | pending |
| C04 | mixed currency scale in invoices | 531 | unit-inconsistency | pending |

## C01 — status column semantic drift  `OK`

The status column has different allowed values in invoices (paid/unpaid/void), subscriptions (active/cancelled/paused), and deals (open/won/lost), causing filtering with values from the wrong table to return zero rows.

**Columns:** `invoices.status`, `subscriptions.status`, `deals.status`

**Evidence:** 2,030

**Proof:**

```sql
SELECT COUNT(*) AS evidence FROM (SELECT 'invoices' as tbl, status FROM invoices WHERE status NOT IN ('active', 'cancelled', 'paused') UNION ALL SELECT 'subscriptions' as tbl, status FROM subscriptions WHERE status NOT IN ('paid', 'unpaid', 'void') UNION ALL SELECT 'deals' as tbl, status FROM deals WHERE status NOT IN ('open', 'won', 'lost')) AS bad_status
```

> FLAGGED: evidence (2030) covers every row of `deals` (300) -- legitimate for a whole-column property, but also what a disguised tautology looks like

**Human decision:** pending

## C02 — plan_tier cache staleness  `OK`

The customers.plan_tier column (cached from billing nightly job) disagrees with the source subscriptions.tier column in 26 rows.

**Columns:** `customers.plan_tier`, `subscriptions.tier`

**Evidence:** 26

**Proof:**

```sql
SELECT COUNT(*) AS evidence FROM customers c LEFT JOIN subscriptions s ON c.customer_id = s.customer_id WHERE c.plan_tier != s.tier OR s.tier IS NULL
```

**Human decision:** pending

## C03 — usage_events event_value text storage  `OK`

The event_value column stores numeric magnitudes as text (varchar), with 1,490 non-numeric junk values mixed in. MySQL coerces these to 0 without error, and comparisons run lexically, causing aggregates and ORDER BY to be silently wrong.

**Columns:** `usage_events.event_value`

**Evidence:** 1,490

**Proof:**

```sql
SELECT COUNT(*) AS evidence FROM usage_events WHERE event_value NOT REGEXP '^[0-9]+(\.[0-9]+)?$'
```

**Human decision:** pending

## C04 — mixed currency scale in invoices  `OK`

The invoices.amount column contains values in two different scales: dollars (currency_minor=0) and cents (currency_minor=1), making amounts incomparable without normalization.

**Columns:** `invoices.amount`, `invoices.currency_minor`

**Evidence:** 531

**Proof:**

```sql
SELECT COUNT(*) AS evidence FROM invoices WHERE currency_minor IS NOT NULL AND currency_minor = 1
```

**Human decision:** pending
