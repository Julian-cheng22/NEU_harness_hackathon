-- Synthetic civil-litigation database (MySQL 8, InnoDB, utf8mb4). All names, parties
-- and cases are fictional. No FOREIGN KEY constraints on purpose (see defect D8).
-- ID ranges are disjoint per entity: courts 10+, judges 100+, law_firms 700+,
-- attorneys 800+, parties 30000+, cases 50000+, docket_entries 200000+,
-- hearings 300000+, fee_invoices 400000+, case_parties 500000+.

DROP TABLE IF EXISTS case_parties;
DROP TABLE IF EXISTS fee_invoices;
DROP TABLE IF EXISTS hearings;
DROP TABLE IF EXISTS docket_entries;
DROP TABLE IF EXISTS cases;
DROP TABLE IF EXISTS parties;
DROP TABLE IF EXISTS attorneys;
DROP TABLE IF EXISTS law_firms;
DROP TABLE IF EXISTS judges;
DROP TABLE IF EXISTS courts;

CREATE TABLE courts (
  court_id  INT PRIMARY KEY,
  name      VARCHAR(80) NOT NULL,
  level     VARCHAR(20) NOT NULL COMMENT 'District | Circuit | State',
  state     CHAR(2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE judges (
  judge_id        INT PRIMARY KEY,
  name            VARCHAR(80) NOT NULL,
  court_id        INT NOT NULL COMMENT 'courts.court_id',
  appointed_date  DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE law_firms (
  firm_id  INT PRIMARY KEY,
  name     VARCHAR(80) NOT NULL,
  city     VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE attorneys (
  attorney_id  INT PRIMARY KEY,
  name         VARCHAR(80) NOT NULL,
  firm_id      INT NOT NULL COMMENT 'law_firms.firm_id',
  bar_year     INT NOT NULL COMMENT 'Year admitted to the bar'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE parties (
  party_id    INT PRIMARY KEY,
  name        VARCHAR(100) NOT NULL,
  party_type  VARCHAR(20) NOT NULL COMMENT 'individual | corporation | government',
  state       CHAR(2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE cases (
  case_id             INT PRIMARY KEY,
  court_id            INT NOT NULL,
  judge_id            INT NOT NULL,
  lead_attorney_id    INT NOT NULL,
  case_type           VARCHAR(20) NOT NULL COMMENT 'civil | contract | employment | ip | personal_injury',
  filed_date          DATE NOT NULL,
  closed_date         DATE NULL,
  status              VARCHAR(20) NOT NULL COMMENT 'OPEN | CLOSED | APPEALED',
  disposition_code    TINYINT NULL COMMENT '1=Judgment for plaintiff, 2=Judgment for defendant, 3=Settled, 4=Dismissed',
  award_amount        VARCHAR(20) NULL COMMENT 'Award or settlement amount in USD',
  num_filings         INT NOT NULL COMMENT 'Cached count of docket entries'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE case_parties (
  cp_id     INT PRIMARY KEY,
  case_id   INT NOT NULL,
  party_id  INT NOT NULL,
  role      VARCHAR(20) NOT NULL COMMENT 'plaintiff | defendant'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE docket_entries (
  entry_id      INT PRIMARY KEY,
  case_no       INT NOT NULL COMMENT 'Case the entry belongs to',
  entry_type    VARCHAR(20) NOT NULL COMMENT 'complaint | answer | motion | order | notice',
  filed_at      DATETIME NOT NULL,
  source        VARCHAR(20) NOT NULL COMMENT 'ECF | CLERK',
  attorney_id   INT NULL COMMENT 'Filing attorney; NULL for court-issued entries'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE hearings (
  hearing_id     INT PRIMARY KEY,
  matter_id      INT NOT NULL COMMENT 'Case being heard',
  scheduled_at   DATETIME NOT NULL,
  hearing_type   VARCHAR(20) NOT NULL COMMENT 'status | motion | trial | settlement',
  status         VARCHAR(20) NOT NULL COMMENT 'SCHEDULED | HELD | CONTINUED | CANCELLED',
  judge_id       INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE fee_invoices (
  invoice_id  INT PRIMARY KEY,
  case_id     INT NOT NULL,
  firm_id     INT NOT NULL,
  billed_at   DATE NOT NULL,
  paid_at     DATE NULL,
  status      VARCHAR(20) NOT NULL COMMENT 'paid | unpaid | void',
  amount      DECIMAL(14,2) NOT NULL COMMENT 'Invoice amount'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
