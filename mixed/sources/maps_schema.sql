-- Synthetic map-listings database (MySQL 8, InnoDB, utf8mb4). All places, owners and
-- users are fictional. No FOREIGN KEY constraints on purpose (see defect D8).
-- ID ranges are disjoint per entity: categories 10+, neighborhoods 40+, owners 900+,
-- places 7000+, users 20000+, reviews 400000+, photos 500000+, flags 600000+,
-- ads 700000+, opening_hours 800000+.

DROP TABLE IF EXISTS opening_hours;
DROP TABLE IF EXISTS ads;
DROP TABLE IF EXISTS flags;
DROP TABLE IF EXISTS photos;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS places;
DROP TABLE IF EXISTS owners;
DROP TABLE IF EXISTS neighborhoods;
DROP TABLE IF EXISTS categories;

CREATE TABLE categories (
  category_id  INT PRIMARY KEY,
  name         VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE neighborhoods (
  neighborhood_id  INT PRIMARY KEY,
  city             VARCHAR(30) NOT NULL,
  name             VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE owners (
  owner_id  INT PRIMARY KEY,
  name      VARCHAR(80) NOT NULL,
  email     VARCHAR(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE places (
  place_id          INT PRIMARY KEY,
  name              VARCHAR(80) NOT NULL,
  address           VARCHAR(100) NOT NULL,
  category_id       INT NOT NULL,
  neighborhood_id   INT NOT NULL,
  owner_id          INT NULL COMMENT 'NULL = listing not yet claimed by a business owner',
  seating_capacity  VARCHAR(12) NULL COMMENT 'Number of seats',
  status            VARCHAR(24) NOT NULL COMMENT 'OPEN | TEMP_CLOSED | CLOSED_PERMANENTLY',
  opened_date       DATE NOT NULL,
  closed_date       DATE NULL,
  avg_rating        DECIMAL(3,2) NOT NULL COMMENT 'Cached average review rating; 0.00 when unreviewed',
  review_count      INT NOT NULL COMMENT 'Cached number of reviews'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
  user_id      INT PRIMARY KEY,
  name         VARCHAR(80) NOT NULL,
  home_city    VARCHAR(30) NOT NULL,
  joined_date  DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reviews (
  review_id    INT PRIMARY KEY,
  place_ref    INT NOT NULL COMMENT 'Place being reviewed',
  reviewer_id  INT NOT NULL COMMENT 'users.user_id',
  rating       VARCHAR(4) NULL COMMENT 'Star rating 1-5',
  text_len     INT NOT NULL,
  created_at   DATETIME NOT NULL,
  source       VARCHAR(20) NOT NULL COMMENT 'MOBILE | WEB'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE photos (
  photo_id     INT PRIMARY KEY,
  poi_id       INT NOT NULL COMMENT 'Place shown in the photo',
  uploader_id  INT NOT NULL,
  uploaded_on  DATE NOT NULL,
  size_kb      INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE flags (
  flag_id      INT PRIMARY KEY,
  listing_id   INT NOT NULL COMMENT 'Place being flagged',
  reporter_id  INT NOT NULL,
  reason_code  TINYINT NOT NULL COMMENT '1=Wrong address, 2=Closed, 3=Duplicate, 4=Inappropriate',
  flagged_on   DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ads (
  ad_id           INT PRIMARY KEY,
  place_id        INT NOT NULL,
  billing_system  VARCHAR(20) NOT NULL COMMENT 'ADS_V2 | LEGACY',
  status          VARCHAR(20) NOT NULL COMMENT 'active | paused | ended',
  spend           DECIMAL(14,2) NOT NULL COMMENT 'Ad spend',
  start_date      DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE opening_hours (
  hours_id    INT PRIMARY KEY,
  place_id    INT NOT NULL,
  weekday     TINYINT NOT NULL COMMENT '0=Sunday .. 6=Saturday',
  open_time   TIME NOT NULL,
  close_time  TIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
