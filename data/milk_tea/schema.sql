-- Synthetic milk-tea benchmark; MySQL 8.0.35. Load into an EMPTY dedicated schema.

CREATE TABLE `materials` (
  `material_id` INT PRIMARY KEY COMMENT '物料编码',
  `material_name` VARCHAR(64) NOT NULL COMMENT '物料名称',
  `base_unit` VARCHAR(12) NOT NULL COMMENT '基本单位',
  `units_per_box` INT NOT NULL COMMENT '箱规',
  `shelf_life_days` INT NOT NULL COMMENT '保质期天数'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `warehouses` (
  `warehouse_id` INT PRIMARY KEY COMMENT '仓库编码',
  `warehouse_name` VARCHAR(64) NOT NULL COMMENT '仓库名称'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `customers` (
  `customer_id` INT PRIMARY KEY COMMENT '客户编码',
  `customer_name` VARCHAR(64) NOT NULL COMMENT '客户名称'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `sales` (
  `row_id` BIGINT PRIMARY KEY COMMENT 'Export row identifier',
  `order_code` VARCHAR(30) NOT NULL COMMENT '单据编号 / document number',
  `line_no` INT NOT NULL COMMENT '单据行号 / line number',
  `order_date` DATE NOT NULL COMMENT '业务日期 / business date',
  `status` VARCHAR(16) NOT NULL COMMENT '单据状态 / document status',
  `material_id` INT NOT NULL COMMENT '物料编码 / material key',
  `warehouse_id` INT NOT NULL COMMENT '仓库编码 / warehouse key',
  `unit` VARCHAR(12) NOT NULL COMMENT '单据单位 / document unit',
  `qty` DECIMAL(18,3) NOT NULL COMMENT '单据数量 / document quantity',
  `batch_no` VARCHAR(24) NOT NULL COMMENT '批号 / batch number',
  `customer_id` INT NOT NULL COMMENT '客户编码',
  `line_net_amount` DECIMAL(18,2) NOT NULL COMMENT '行未税金额 CNY',
  `line_tax_amount` DECIMAL(18,2) NOT NULL COMMENT '行价税合计 CNY',
  `order_total` DECIMAL(18,2) NOT NULL COMMENT '订单价税合计 CNY',
  INDEX ix_material (material_id),
  INDEX ix_warehouse (warehouse_id),
  INDEX ix_order (order_code),
  INDEX ix_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `sales_returns` (
  `row_id` BIGINT PRIMARY KEY COMMENT 'Export row identifier',
  `order_code` VARCHAR(30) NOT NULL COMMENT '单据编号 / document number',
  `line_no` INT NOT NULL COMMENT '单据行号 / line number',
  `order_date` DATE NOT NULL COMMENT '业务日期 / business date',
  `status` VARCHAR(16) NOT NULL COMMENT '单据状态 / document status',
  `material_id` INT NOT NULL COMMENT '物料编码 / material key',
  `warehouse_id` INT NOT NULL COMMENT '仓库编码 / warehouse key',
  `unit` VARCHAR(12) NOT NULL COMMENT '单据单位 / document unit',
  `qty` DECIMAL(18,3) NOT NULL COMMENT '单据数量 / document quantity',
  `batch_no` VARCHAR(24) NOT NULL COMMENT '批号 / batch number',
  `sale_line_id` BIGINT NOT NULL COMMENT '原销售行标识',
  `source_system` VARCHAR(16) NOT NULL COMMENT '来源系统',
  `refund_net` DECIMAL(18,2) NOT NULL COMMENT '退货未税金额 CNY',
  INDEX ix_material (material_id),
  INDEX ix_warehouse (warehouse_id),
  INDEX ix_order (order_code),
  INDEX ix_status (status),
  INDEX ix_sale (sale_line_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `stock_in` (
  `row_id` BIGINT PRIMARY KEY COMMENT 'Export row identifier',
  `order_code` VARCHAR(30) NOT NULL COMMENT '单据编号 / document number',
  `line_no` INT NOT NULL COMMENT '单据行号 / line number',
  `order_date` DATE NOT NULL COMMENT '业务日期 / business date',
  `status` VARCHAR(16) NOT NULL COMMENT '单据状态 / document status',
  `material_id` INT NOT NULL COMMENT '物料编码 / material key',
  `warehouse_id` INT NOT NULL COMMENT '仓库编码 / warehouse key',
  `unit` VARCHAR(12) NOT NULL COMMENT '单据单位 / document unit',
  `qty` DECIMAL(18,3) NOT NULL COMMENT '单据数量 / document quantity',
  `batch_no` VARCHAR(24) NOT NULL COMMENT '批号 / batch number',
  `movement_key` VARCHAR(32) NOT NULL COMMENT '业务流水标识',
  `source_type` VARCHAR(20) NOT NULL COMMENT '来源单据类型',
  `source_line_id` BIGINT COMMENT '来源单据行标识',
  `posted_at` DATETIME COMMENT '过账时间',
  INDEX ix_material (material_id),
  INDEX ix_warehouse (warehouse_id),
  INDEX ix_order (order_code),
  INDEX ix_status (status),
  INDEX ix_movement (movement_key),
  INDEX ix_source (source_type, source_line_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `stock_out` (
  `row_id` BIGINT PRIMARY KEY COMMENT 'Export row identifier',
  `order_code` VARCHAR(30) NOT NULL COMMENT '单据编号 / document number',
  `line_no` INT NOT NULL COMMENT '单据行号 / line number',
  `order_date` DATE NOT NULL COMMENT '业务日期 / business date',
  `status` VARCHAR(16) NOT NULL COMMENT '单据状态 / document status',
  `material_id` INT NOT NULL COMMENT '物料编码 / material key',
  `warehouse_id` INT NOT NULL COMMENT '仓库编码 / warehouse key',
  `unit` VARCHAR(12) NOT NULL COMMENT '单据单位 / document unit',
  `qty` DECIMAL(18,3) NOT NULL COMMENT '单据数量 / document quantity',
  `batch_no` VARCHAR(24) NOT NULL COMMENT '批号 / batch number',
  `source_type` VARCHAR(20) NOT NULL COMMENT '出库类型',
  `sale_line_id` BIGINT COMMENT '来源销售行标识',
  `weight_text` VARCHAR(20) COMMENT '实测重量 kg',
  INDEX ix_material (material_id),
  INDEX ix_warehouse (warehouse_id),
  INDEX ix_order (order_code),
  INDEX ix_status (status),
  INDEX ix_sale (sale_line_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `production_receipts` (
  `row_id` BIGINT PRIMARY KEY COMMENT 'Export row identifier',
  `order_code` VARCHAR(30) NOT NULL COMMENT '单据编号 / document number',
  `line_no` INT NOT NULL COMMENT '单据行号 / line number',
  `order_date` DATE NOT NULL COMMENT '业务日期 / business date',
  `status` VARCHAR(16) NOT NULL COMMENT '单据状态 / document status',
  `material_id` INT NOT NULL COMMENT '物料编码 / material key',
  `warehouse_id` INT NOT NULL COMMENT '仓库编码 / warehouse key',
  `unit` VARCHAR(12) NOT NULL COMMENT '单据单位 / document unit',
  `qty` DECIMAL(18,3) NOT NULL COMMENT '单据数量 / document quantity',
  `batch_no` VARCHAR(24) NOT NULL COMMENT '批号 / batch number',
  `production_order` VARCHAR(30) NOT NULL COMMENT '生产工单',
  `prd_date` DATE NOT NULL COMMENT '生产日期',
  `exp_date` DATE NOT NULL COMMENT '有效期至',
  INDEX ix_material (material_id),
  INDEX ix_warehouse (warehouse_id),
  INDEX ix_order (order_code),
  INDEX ix_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
