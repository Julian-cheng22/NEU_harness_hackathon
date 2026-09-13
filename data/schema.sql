-- ============================================================================
-- Synthetic B2B SaaS internal-ops schema  (10 tables)
--
-- WARNING TO READERS: the "flaws" in this schema are INTENTIONAL.
-- Every oddity below is a deliberate defect (D1..D10) from data/defects.yaml,
-- injected so we can measure whether the harness helps a weak model cope.
-- Do not "fix" anything here without updating defects.yaml and questions.yaml.
--
-- Notable on purpose:
--   * NO FOREIGN KEY CONSTRAINTS are declared anywhere (D8 needs orphan rows,
--     and it forces the harness's infer_joins tool to do real work).
--   * The customer key is spelled three different ways across tables (D1).
--   * `status` means different things on different tables (D2).
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS churn_log;
DROP TABLE IF EXISTS product_catalog;
DROP TABLE IF EXISTS campaigns;
DROP TABLE IF EXISTS deals;
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS support_tickets;
DROP TABLE IF EXISTS usage_events;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS customers;

-- ---------------------------------------------------------------------------
-- 1. customers  -- master account list
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
  customer_id        INT           NOT NULL,
  company_name       VARCHAR(120)  NOT NULL,
  industry           VARCHAR(60)       NULL,
  country            VARCHAR(60)       NULL,
  signup_date        DATE              NULL,
  -- D3: STALE denormalized copy of subscriptions.tier. Refreshed by a nightly
  -- job that has been silently failing. Disagrees with the live value for a
  -- meaningful fraction of rows. subscriptions.tier is the source of truth.
  plan_tier          VARCHAR(20)       NULL COMMENT 'Cached from billing nightly job.',
  -- D1: references employees.employee_id but is not named *_employee_id.
  account_manager_id INT               NULL,
  is_active          TINYINT(1)    NOT NULL DEFAULT 1,
  created_at         DATETIME          NULL,
  PRIMARY KEY (customer_id),
  KEY idx_customers_company (company_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 2. subscriptions  -- live subscription state (source of truth for tier/MRR)
-- ---------------------------------------------------------------------------
CREATE TABLE subscriptions (
  subscription_id INT          NOT NULL,
  customer_id     INT              NULL,
  tier            VARCHAR(20)      NULL COMMENT 'Source of truth for plan tier.',
  seats           INT              NULL,
  -- Unambiguous units here, which is exactly what makes invoices.amount worse.
  mrr_cents       INT              NULL COMMENT 'Monthly recurring revenue in USD cents.',
  started_on      DATE             NULL,
  -- NULL here genuinely means "still running" -- contrast with invoices.paid_at (D4).
  ended_on        DATE             NULL,
  -- D2: 'active' | 'paused' | 'cancelled'
  status          VARCHAR(20)      NULL,
  updated_at      DATETIME         NULL,
  PRIMARY KEY (subscription_id),
  KEY idx_subs_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 3. invoices  -- billing documents
-- ---------------------------------------------------------------------------
CREATE TABLE invoices (
  invoice_id      INT            NOT NULL,
  subscription_id INT                NULL,
  -- D1: same concept as customers.customer_id, different spelling.
  cust_id         INT                NULL,
  -- D10: UNIT TRAP. Rows written by the legacy biller store minor units (cents);
  -- rows from the current biller store major units (dollars). currency_minor
  -- tells you which -- except when it is NULL, which is ~15% of rows.
  amount          DECIMAL(12,2)      NULL,
  currency_minor  TINYINT(1)         NULL COMMENT '1=amount is in cents, 0=amount is in dollars, NULL=unknown',
  -- D10: SEMANTIC TRAP. invoices.amount EXCLUDES tax; deals.amount INCLUDES it.
  tax_included    TINYINT(1)         NULL DEFAULT 0,
  issued_at       DATETIME           NULL,
  -- D4: AMBIGUOUS NULL. NULL means EITHER "not paid yet" OR "paid, but the
  -- payment webhook never backfilled the timestamp". status disambiguates --
  -- partially. Counting NULLs as unpaid overstates receivables.
  paid_at         DATETIME           NULL,
  -- D2: 'paid' | 'unpaid' | 'void'  -- NOT the same vocabulary as deals.status
  status          VARCHAR(20)        NULL,
  PRIMARY KEY (invoice_id),
  KEY idx_inv_cust (cust_id),
  KEY idx_inv_sub (subscription_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 4. usage_events  -- product telemetry
-- ---------------------------------------------------------------------------
CREATE TABLE usage_events (
  event_id    BIGINT       NOT NULL,
  customer_id INT              NULL,
  feature_key VARCHAR(60)      NULL,
  -- D5: TYPE DRIFT. Declared VARCHAR, holds '12', '12.0', 'N/A', '' and NULL.
  -- CAST/SUM will silently coerce junk to 0 rather than error.
  event_value VARCHAR(32)      NULL COMMENT 'Numeric magnitude, stored as text.',
  -- D7: TIMEZONE TRAP. No tz column. source='api' rows are UTC;
  -- source='batch' rows were written in US/Eastern local time. Day-boundary
  -- and "last 24h" questions get the wrong answer if you ignore this.
  event_ts    DATETIME         NULL,
  source      VARCHAR(20)      NULL COMMENT 'api | web | batch',
  PRIMARY KEY (event_id),
  KEY idx_usage_cust_ts (customer_id, event_ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 5. support_tickets
-- ---------------------------------------------------------------------------
CREATE TABLE support_tickets (
  ticket_id   INT           NOT NULL,
  -- D1 + D8: third spelling of the customer key, AND some values point at
  -- customer_ids that no longer exist (hard-deleted for GDPR, never cascaded).
  -- An INNER JOIN silently drops these tickets; a COUNT(*) does not.
  cust_id     INT               NULL,
  assigned_to INT               NULL,
  opened_at   DATETIME          NULL,
  resolved_at DATETIME          NULL,
  priority    VARCHAR(4)        NULL COMMENT 'P1 | P2 | P3 | P4',
  csat        TINYINT           NULL COMMENT '1-5, NULL if no survey response',
  subject     VARCHAR(200)      NULL,
  PRIMARY KEY (ticket_id),
  KEY idx_tickets_cust (cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 6. employees
-- ---------------------------------------------------------------------------
CREATE TABLE employees (
  employee_id INT           NOT NULL,
  full_name   VARCHAR(120)      NULL,
  team        VARCHAR(60)       NULL,
  hired_on    DATE              NULL,
  -- NULL = still employed. Departed AMs still own accounts in customers.
  left_on     DATE              NULL,
  manager_id  INT               NULL,
  PRIMARY KEY (employee_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 7. deals  -- CRM pipeline
-- ---------------------------------------------------------------------------
CREATE TABLE deals (
  deal_id     INT            NOT NULL,
  -- D1: THIRD spelling of the customer key. Name similarity alone will not
  -- find this join -- value-overlap analysis will.
  acct_id     INT                NULL,
  owner_id    INT                NULL,
  -- D10: includes tax and is always in DOLLARS. Comparing this to
  -- invoices.amount without normalizing is the single easiest way to be wrong.
  amount      DECIMAL(12,2)      NULL COMMENT 'Deal value in USD, tax INCLUSIVE.',
  stage       VARCHAR(40)        NULL,
  -- D2: 'open' | 'won' | 'lost'  -- same column name as invoices.status,
  -- entirely different value domain.
  status      VARCHAR(20)        NULL,
  campaign_id INT                NULL,
  -- D1: yet another date-naming convention (close_dt vs signup_date vs started_on).
  close_dt    DATE               NULL,
  PRIMARY KEY (deal_id),
  KEY idx_deals_acct (acct_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 8. campaigns  -- marketing
-- ---------------------------------------------------------------------------
CREATE TABLE campaigns (
  campaign_id INT            NOT NULL,
  name        VARCHAR(120)       NULL,
  channel     VARCHAR(40)        NULL,
  budget_usd  DECIMAL(12,2)      NULL,
  start_date  DATE               NULL,
  end_date    DATE               NULL,
  PRIMARY KEY (campaign_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 9. product_catalog  -- SCD-2 style versioned price list
-- ---------------------------------------------------------------------------
CREATE TABLE product_catalog (
  catalog_id     INT            NOT NULL,
  sku            VARCHAR(40)        NULL,
  product_name   VARCHAR(120)       NULL,
  tier           VARCHAR(20)        NULL COMMENT 'Joins to subscriptions.tier',
  list_price_usd DECIMAL(10,2)      NULL,
  -- OUTDATED DATA: each sku has several rows over time. Joining on sku alone
  -- multiplies rows and picks up superseded prices.
  effective_from DATE               NULL,
  effective_to   DATE               NULL COMMENT 'NULL = currently in effect',
  -- D3: second stale flag. Disagrees with effective_to for some SKUs after a
  -- botched backfill. effective_to is authoritative.
  is_current     TINYINT(1)         NULL,
  PRIMARY KEY (catalog_id),
  KEY idx_catalog_sku (sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 10. churn_log
-- ---------------------------------------------------------------------------
CREATE TABLE churn_log (
  churn_id    INT       NOT NULL,
  customer_id INT           NULL,
  churned_on  DATE          NULL,
  -- D9: UNDOCUMENTED ENUM. The comment documents codes 1-4 only, but the data
  -- contains codes 1-9. There is no lookup table. A model that trusts the
  -- comment will confidently mislabel every row with code >= 5.
  reason_code TINYINT       NULL COMMENT '1=price 2=missing_features 3=support 4=competitor',
  recovered_on DATE         NULL COMMENT 'Non-NULL = won back; they are NOT still churned',
  PRIMARY KEY (churn_id),
  KEY idx_churn_cust (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS = 1;
