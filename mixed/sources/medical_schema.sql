-- Synthetic hospital database (MySQL 8, InnoDB, utf8mb4).
-- No FOREIGN KEY constraints are declared on purpose (see defect D8).
-- ID ranges are disjoint per entity so join inference cannot be fooled by
-- accidental value overlap: departments 10+, providers 200+, patients 10000+,
-- admissions 20000+, lab_items 50000+, lab_events 100000+, diagnoses 300000+,
-- prescriptions 400000+, procedures 500000+, claims 600000+.

DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS procedures;
DROP TABLE IF EXISTS prescriptions;
DROP TABLE IF EXISTS lab_events;
DROP TABLE IF EXISTS lab_items;
DROP TABLE IF EXISTS diagnoses;
DROP TABLE IF EXISTS admissions;
DROP TABLE IF EXISTS patients;
DROP TABLE IF EXISTS providers;
DROP TABLE IF EXISTS departments;

CREATE TABLE departments (
  dept_id      INT PRIMARY KEY,
  name         VARCHAR(60) NOT NULL COMMENT 'Clinical department name'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE providers (
  provider_id  INT PRIMARY KEY,
  name         VARCHAR(80) NOT NULL,
  dept_id      INT NOT NULL COMMENT 'departments.dept_id',
  role         VARCHAR(20) NOT NULL COMMENT 'physician | nurse | technician',
  hire_date    DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE patients (
  subject_id        INT PRIMARY KEY COMMENT 'Patient surrogate key',
  name              VARCHAR(80) NOT NULL,
  dob               DATE NOT NULL,
  sex               CHAR(1) NOT NULL COMMENT 'M | F',
  insurance         VARCHAR(20) NOT NULL COMMENT 'Medicare | Medicaid | Private | Self-pay',
  total_admissions  INT NOT NULL COMMENT 'Cached count of admissions',
  last_dept_id      INT NULL COMMENT 'Cached department of most recent admission'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE admissions (
  hadm_id               INT PRIMARY KEY,
  subject_id            INT NOT NULL,
  admittime             DATETIME NOT NULL,
  dischtime             DATETIME NULL,
  admission_type        VARCHAR(20) NOT NULL COMMENT 'EMERGENCY | ELECTIVE | URGENT',
  status                VARCHAR(20) NOT NULL COMMENT 'ADMITTED | DISCHARGED | EXPIRED',
  discharge_location    VARCHAR(20) NOT NULL,
  discharge_disposition TINYINT NULL COMMENT '1=Home, 2=Skilled nursing, 3=Rehab, 4=Died',
  dept_id               INT NOT NULL,
  attending_id          INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE diagnoses (
  dx_id        INT PRIMARY KEY,
  hadm_id      INT NOT NULL,
  subject_id   INT NOT NULL,
  icd_code     VARCHAR(10) NOT NULL,
  seq_num      INT NOT NULL COMMENT '1 = primary diagnosis'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE lab_items (
  itemid       INT PRIMARY KEY,
  label        VARCHAR(40) NOT NULL,
  fluid        VARCHAR(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE lab_events (
  labevent_id  INT PRIMARY KEY,
  subject_id   INT NOT NULL,
  hadm_id      INT NOT NULL,
  itemid       INT NOT NULL,
  charttime    DATETIME NOT NULL,
  value        VARCHAR(20) NULL COMMENT 'Result as reported',
  valuenum     DECIMAL(10,3) NULL COMMENT 'Parsed numeric result when available',
  uom          VARCHAR(10) NULL COMMENT 'Unit of measure',
  source       VARCHAR(20) NOT NULL COMMENT 'EHR_ENTRY | LIS_BATCH'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE prescriptions (
  rx_id        INT PRIMARY KEY,
  pat_id       INT NOT NULL,
  hadm_id      INT NOT NULL,
  drug         VARCHAR(40) NOT NULL,
  start_time   DATETIME NOT NULL,
  provider_id  INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE procedures (
  proc_id      INT PRIMARY KEY,
  hadm_id      INT NOT NULL,
  subject_id   INT NOT NULL,
  proc_code    VARCHAR(10) NOT NULL,
  performed_at DATETIME NOT NULL,
  provider_id  INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE claims (
  claim_id     INT PRIMARY KEY,
  patient_id   INT NOT NULL,
  hadm_id      INT NOT NULL,
  billed_at    DATE NOT NULL,
  paid_at      DATE NULL,
  status       VARCHAR(20) NOT NULL COMMENT 'submitted | paid | denied | void',
  amount       DECIMAL(12,2) NOT NULL COMMENT 'Claim amount'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
