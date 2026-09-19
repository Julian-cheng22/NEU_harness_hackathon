-- Synthetic e-commerce order database (MySQL 8, InnoDB, utf8mb4). All customers and
-- products are fictional. No FOREIGN KEY constraints on purpose (see defect D8).
-- ID ranges are disjoint per entity: categories 10+, warehouses 50+, products 1000+,
-- customers 20000+, orders 60000+, reviews 400000+, order_items 700000+,
-- payments 800000+, shipments 900000+, returns 950000+.

DROP TABLE IF EXISTS returns;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS shipments;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS warehouses;
DROP TABLE IF EXISTS categories;

CREATE TABLE categories (
  category_id  INT PRIMARY KEY,
  name         VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE warehouses (
  warehouse_id  INT PRIMARY KEY,
  name          VARCHAR(60) NOT NULL,
  state         CHAR(2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE products (
  product_id   INT PRIMARY KEY,
  name         VARCHAR(80) NOT NULL,
  category_id  INT NOT NULL COMMENT 'categories.category_id',
  price        DECIMAL(10,2) NOT NULL COMMENT 'List price in USD',
  weight_kg    VARCHAR(12) NULL COMMENT 'Shipping weight in kilograms',
  avg_rating   DECIMAL(3,2) NOT NULL COMMENT 'Cached average review rating; 0.00 when unreviewed'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customers (
  customer_id   INT PRIMARY KEY,
  name          VARCHAR(80) NOT NULL,
  email         VARCHAR(100) NOT NULL,
  signup_date   DATE NOT NULL,
  state         CHAR(2) NOT NULL,
  order_count   INT NOT NULL COMMENT 'Cached number of orders placed'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
  order_id      INT PRIMARY KEY,
  customer_id   INT NOT NULL,
  placed_at     DATETIME NOT NULL,
  source        VARCHAR(20) NOT NULL COMMENT 'WEB | CALL_CENTER',
  status        VARCHAR(20) NOT NULL COMMENT 'PLACED | SHIPPED | DELIVERED | CANCELLED',
  shipping_fee  DECIMAL(8,2) NOT NULL,
  total_amount  DECIMAL(12,2) NOT NULL COMMENT 'Items plus shipping, USD'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_items (
  item_id     INT PRIMARY KEY,
  order_id    INT NOT NULL,
  product_id  INT NOT NULL,
  quantity    INT NOT NULL,
  unit_price  DECIMAL(10,2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE payments (
  payment_id  INT PRIMARY KEY,
  order_id    INT NOT NULL,
  gateway     VARCHAR(20) NOT NULL COMMENT 'STRIPE | LEGACY',
  status      VARCHAR(20) NOT NULL COMMENT 'captured | failed | refunded',
  amount      DECIMAL(14,2) NOT NULL COMMENT 'Payment amount'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE shipments (
  shipment_id   INT PRIMARY KEY,
  order_ref     INT NOT NULL COMMENT 'Order being shipped',
  warehouse_id  INT NOT NULL,
  carrier       VARCHAR(20) NOT NULL COMMENT 'UPS | FedEx | USPS',
  shipped_at    DATETIME NOT NULL,
  delivered_at  DATETIME NULL,
  status        VARCHAR(20) NOT NULL COMMENT 'IN_TRANSIT | DELIVERED | LOST'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE returns (
  return_id      INT PRIMARY KEY,
  buyer_id       INT NOT NULL COMMENT 'Customer who returned the item',
  item_id        INT NOT NULL COMMENT 'order_items.item_id',
  returned_on    DATE NOT NULL,
  reason_code    TINYINT NOT NULL COMMENT '1=Defective, 2=Wrong item, 3=Changed mind, 4=Damaged in transit',
  refund_amount  DECIMAL(10,2) NOT NULL COMMENT 'USD'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reviews (
  review_id      INT PRIMARY KEY,
  user_id        INT NOT NULL COMMENT 'Reviewing customer',
  product_id     INT NOT NULL,
  rating         VARCHAR(4) NULL COMMENT 'Star rating 1-5',
  created_on     DATE NOT NULL,
  helpful_votes  INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
