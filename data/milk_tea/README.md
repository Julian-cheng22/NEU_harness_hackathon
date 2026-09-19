# 奶茶销售与库存模拟数据 / Milk-tea benchmark

Version `milk-tea-1.0.0`, seed `20260919`, MySQL **8.0.35**.

**10,000 行**包括 9,950 条单据明细和 50 条主数据；不是 10,000 张单据，
也不是 10,000 条异常记录。正常记录与有缺陷的导出记录共存。
所有名称、单号、数量和金额均程序生成，无真实客户、订单或交易数据。

## 场景与数据量

模拟中央厨房生产奶茶基底杯、仓库配送、门店订货及退货。成品按杯或箱流转，
每个 SKU 的箱规为 6、12、24 或 48 杯。库存入库包含采购、生产和销售退货。
本版不模拟原料采购单、BOM、原料耗用、生产成本、支付或会计收入确认。
销售单表示已确认订单；只有库存单过账才改变实物库存。

| 表 | 单据／主数据 | 行数 | 粒度 |
|---|---|---:|---|
| `sales` | 销售单 | 4,000 | 每单两行，共 2,000 张单据 |
| `sales_returns` | 销售退货单 | 600 | 每单一行，关联原销售明细 |
| `stock_in` | 库存入库单 | 2,200 | 2,160 条业务流水及 40 条导入重放 |
| `stock_out` | 库存出库单 | 2,200 | 每单一行，销售发货或报损 |
| `production_receipts` | 生产入库单 | 950 | 每单一行，关联对应库存入库 |
| `materials` | 物料／箱规 | 20 | 每 SKU 一行 |
| `warehouses` | 仓库 | 4 | 每仓一行 |
| `customers` | 客户门店 | 26 | 每店一行 |
| **总计** | | **10,000** | |

固定日期范围：2026-09-01 至 2026-09-18；分析截至 2026-09-19。
单据前期初为零，9 月 1 日采购入库提供期初货源。生产 9 月 5 日，销售 9 月
10–14 日，出库 9 月 16 日，退货 9 月 18 日。每个仓库／物料／批次在各事件后
库存非负；已批准退货均有此前已过账销售出库，退货数量、金额不超过原销售行。
虚构保质期为 30 天，虚构税率为 6%；它们只是本实验参数。

## 字段与关联

字段结构参考了本地供应链字段资料中的单号、单据明细、物料、仓库、数量、单位、
批次、金额等通用概念。仅借鉴字段结构，未复制原始数据或公司业务口径。
生产单据结构与全部转换规则由本实验定义，不宣称还原真实企业系统。

完整字段类型和中英文说明见 [schema.sql](schema.sql)。

| 字段组 | 字段 | 含义 |
|---|---|---|
| 单据标识 | `row_id`, `order_code`, `line_no` | 导出行 ID、单号、明细行号；行数不等于单数 |
| 时点与状态 | `order_date`, `status`, `posted_at` | 业务日期、各系统状态、可能缺失的过账时间 |
| 关联维度 | `material_id`, `warehouse_id`, `customer_id` | 物料、仓库、门店 |
| 数量 | `qty`, `unit`, `materials.units_per_box` | 单据数量、单位与箱规；换算到 cup |
| 金额 | `line_net_amount`, `line_tax_amount`, `order_total` | CNY 行未税、行含税、重复在行上的表头含税金额 |
| 退货 | `sale_line_id`, `source_system`, `refund_net` | 原销售行、来源系统、未税退款金额 |
| 入库来源 | `movement_key`, `source_type`, `source_line_id` | 业务流水及来源单据行 |
| 批次 | `batch_no`, `prd_date`, `exp_date` | 批号、生产日期、到期日期 |
| 测量值 | `weight_text` | kg 文本读数，含不可用占位符 |

`sales_returns.sale_line_id` / `stock_out.sale_line_id` → `sales.row_id`。
`stock_in.source_type='production'` 时 `source_line_id` → `production_receipts.row_id`；
`source_type='sales_return'` 时 → `sales_returns.row_id`。
采购入库没有另建采购单，因此 `source_line_id` 为 NULL。
生产、退货源单与库存入库记录表示同一个实物事件。

## 八类缺陷及测试题

| ID | 缺陷／易错语义 | 影响的导出行数 | 题目 |
|---|---|---:|---|
| MT01 | 箱／杯混合，SKU 箱规不同 | 1,781 | MTQ01–02 |
| MT02 | 草稿／作废混入，各系统状态编码不同 | 1,303 非有效状态行 | MTQ03–04 |
| MT03 | legacy 退货量和退款为负，ERP 为正 | 150 | MTQ05–06 |
| MT04 | 入库流水被重复导入，导出行 ID 不同 | 40 多余行 | MTQ07–08 |
| MT05 | 表头订单金额在每条明细重复 | 4,000 | MTQ09–10 |
| MT06 | 生产单与库存入库镜像导致重复计数 | 950 入库镜像行 | MTQ11–12 |
| MT07 | 重量文本混有 N/A 和空串 | 130 | MTQ13–14 |
| MT08 | 已过账数据丢失时间戳 | 154，含重放行 | MTQ15–16 |

MTQ17–19 是正常对照题，MTQ20 是库存余额组合题。
这些集合会重叠，行数不能相加当作“总脏行数”。MT05/06 属于真实导出粒度与
关联语义陷阱，并不意味着这些来源业务记录本身不合法。

[defects.yaml](defects.yaml) 记录业务规则和证据 SQL；
[injection_manifest.json](injection_manifest.json) 记录每类涉及的具体行 ID；
[manifest.json](manifest.json) 记录行数、版本和生成文件 SHA-256。
后三者用于人工审计与测试，不载入 agent 数据库。

[questions.yaml](questions.yaml) 包含英文提问、中文对照、gold SQL、naive SQL
和冻结的 `expect`。8 类各至少两题，共 20 题；17 条 naive 查询均须成功执行，
且在真实 MySQL 上与 gold 结果不同。naive 是人工编写的反例，不是实测模型输出。

`expect` 由生成器对业务事件用 Python Decimal 独立计算，不通过 gold SQL 回填。
MySQL 测试严格比较 SQL 数值和该独立答案，避免 gold 自己对自己评分。
生成器只更改文件；`--check` 按字节检查漂移，不更新答案。
修改生成逻辑时需升级版本，审查新的 SQL／expect／hash diff。

## 生成与导入

仓库根目录执行，要求 Python 3.11+。以下为 macOS/Linux 命令；Windows 将
`.venv/bin/python` 替换为 `.venv\Scripts\python`，用 `$env:NAME='value'` 设置环境变量。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m data.milk_tea.generate --check
```

首次需要生成文件或有意改生成逻辑时：

```sh
.venv/bin/python -m data.milk_tea.generate
git diff -- data/milk_tea
```

启动已有 Docker MySQL（首次使用先将 `.env.example` 复制为 `.env`）：

```sh
docker compose up -d --wait
```

按实际环境设置 `DB_HOST`、`DB_PORT`、`DB_ADMIN_USER`、`DB_ADMIN_PASSWORD`。
仓库 Docker 默认 root 密码为 `.env` 中的 `MYSQL_ROOT_PASSWORD`；Windows 可能使用 3307。
导入器读取 `.env`，创建独立数据库并授予 `DB_USER` 只读权限：

```sh
.venv/bin/python -m data.milk_tea.load --database milk_tea
```

导入器只接受 `milk_tea` 或 `milk_tea_<suffix>`，检测到非空数据库就拒绝，绝不清空
旧表。再次验证可用新名称，例如 `milk_tea_v2`，并同步设置 `DB_NAME` 与
`MILK_TEA_DB_NAME`。现有只读账号的密码不会被重置，应沿用其实际密码。

测试导入数据及金标准：

```sh
export HARNESS_DATASET=milk_tea
export DB_NAME=milk_tea
export MILK_TEA_TEST_DB=1
.venv/bin/python -m pytest tests/test_milk_tea.py -q
.venv/bin/python eval/run.py --arm dry
```

没设置 `MILK_TEA_TEST_DB=1` 时只运行无需数据库的测试，数据库测试明确显示 skipped。
完整验证必须开启该变量。`--arm dry` 应为 **20/20**，但 dry 本身只证明执行链路，
独立答案校验由上述 pytest 负责。

## 接入 harness 与看板

```sh
export HARNESS_DATASET=milk_tea
export DB_NAME=milk_tea
export HARNESS_KNOWLEDGE=curated
.venv/bin/python eval/run.py --arm both  # 需要配置并启动模型；可能产生 API 费用
.venv/bin/python eval/report.py
.venv/bin/python -m web.server
```

新评测结果写入 `eval/out/milk_tea/`；SaaS 仍使用 `eval/out/`。
Web server 自动选择该数据集的问题和结果。切换环境变量后需重启 server。
仓库现有 `web/static/data.js` 仍是 SaaS 的历史离线结果；奶茶请使用启动后的 Web
server，或在选定奶茶环境变量后手动运行 `python -m web.build_data` 重建离线文件
（它会更改 `web/static/data.js`，不要把新的离线文件误当作 SaaS 结果）。
没有真实 A/B 结果时页面不显示准确率提升；gold dry 不是模型准确率。

`harness/milk_tea.py` 提供人工维护的单位、状态、退货、入库、订单金额、生产和重量
词条；通用 profiler 负责类型漂移和 NULL 状态分布。`curated` 为推荐初始模式。
`discovered` 不会读取这些人工词条，也不会误读 SaaS 的学习文件；若尚未运行 discovery，
奶茶学习文件为空。自动发现与实际小模型 A/B 效果不包含在本数据包的验证结果中。

原始 SaaS 数据、问题、gold 和已提交模型结果保持独立。运行完整旧测试时切回
`HARNESS_DATASET=saas DB_NAME=harness`；不要对奶茶数据库直接运行全部旧业务测试。

## 合并前复核

```sh
.venv/bin/python -m data.milk_tea.generate --check
MILK_TEA_TEST_DB=1 .venv/bin/python -m pytest tests/test_milk_tea.py -q
HARNESS_DATASET=milk_tea DB_NAME=milk_tea .venv/bin/python eval/run.py --arm dry
HARNESS_DATASET=saas DB_NAME=harness .venv/bin/python -m pytest tests/ --ignore=tests/test_milk_tea.py -q
```

本包另修复两个在接入时实际触发的问题：`httpx==0.28.1` 满足已有
`google-genai==2.23.0` 的依赖；EXPLAIN 按 SELECT block 估算工作量，避免把
独立汇总子查询误当作笛卡尔积，同时保留对真实无条件 join 的拦截。
