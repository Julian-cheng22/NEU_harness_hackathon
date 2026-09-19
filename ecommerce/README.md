# 电商订单版数据集（ecommerce/）

一个合成的电商订单数据库，所有客户和商品都是虚构的。时间为近期：2024-11-04 至 2025-03-08（覆盖黑色星期五和圣诞季）。harness 代码未做任何修改。用法和 `medical/`、`legal/` 完全相同。

## 目录内容

| 文件 | 说明 |
|---|---|
| `schema.sql` | 10 张表（类别、仓库、商品、客户、订单、订单项、支付、发货、退货、评价），MySQL 格式，故意不加外键 |
| `generate.py` | 固定随机种子生成 `seed.sql`；`--check` 验证结果是否一致 |
| `seed.sql` | 约 0.3 MB：208 名客户、900 个订单、2257 条订单项、858 个包裹、545 条评价 |
| `defects.yaml` | 10 个缺陷（D1–D10），与原项目一一对应，`harness_tool` 沿用原来的 |
| `questions.yaml` | 31 道题：Q01–Q25 缺陷题，C01–C06 对照题，字段格式与原来一致 |

## 缺陷对应

| ID | 电商场景 |
|---|---|
| D1 | 客户/订单 ID 命名不一致：`customer_id` / `user_id` / `buyer_id`，`order_id` / `order_ref` |
| D2 | `orders.status` 与 `payments.status` 同名不同取值域 |
| D3 | `customers.order_count`、`products.avg_rating` 是过期缓存 |
| D4 | `shipments.delivered_at` 为空有两种含义：仍在运输，或包裹已丢失 |
| D5 | `reviews.rating`、`products.weight_kg` 是文本，混有 `N/A`、`varies`、空串 |
| D6 | 同一客户（同 email）用不同 `customer_id` 重复注册 |
| D7 | `source='WEB'` 的下单时间是 UTC（UTC = 本地 + 5 小时），呼叫中心为美东本地时间 |
| D8 | 订单的 `customer_id` 找不到客户（账号已删除） |
| D9 | 退货原因代码文档只写了 1–4，实际有 5–9（遗留系统代码） |
| D10 | `gateway='LEGACY'` 的支付金额单位是"分"，`STRIPE` 是美元 |

数据窗口全部在美东标准时间内（夏令时 2024-11-03 结束、2025-03-09 开始），所以不需要处理夏令时。

## 已验证

在临时 MySQL 里导入 `seed.sql` 后，全部 31 道题的标准答案、25 道缺陷题的错误写法、10 个缺陷检测语句都能执行；10 个缺陷的检测结果均大于 0；除 Q21（只有标准答案）外，缺陷题的标准答案与错误写法结果都不同。`generate.py --check` 通过。

## 没有验证（需要确认）

和另外两个数据集一样：没有读 harness 代码，也没跑过 `eval/run.py`。请确认代码里有没有写死原来的表名或列名，Glossary 是否需要补 D7、D9、D10 的口径，以及 `learned_schema` 是否需要重新生成。

## 已知的小问题

- Q17（7 对 6）、Q21（答案只有 5）的差距很小。
- Q01、Q02、Q03 的错误写法会直接报"列不存在"，这是设计意图，考的是 `user_id` / `buyer_id` / `order_ref` 的命名陷阱。
- 部分题目要求"只统计现有客户"，因为已删除账号的订单（D8）和退货仍然存在。

## 建议的跑法

路径请按实际项目结构调整。

```bash
docker compose down -v && docker compose up -d
docker compose exec -T mysql mysql -uroot -phackathon harness < ecommerce/seed.sql
```

再把 `ecommerce/questions.yaml`、`ecommerce/defects.yaml` 复制成评测读取的 `data/` 下的同名文件（先备份原文件），然后跑 `eval/run.py --arm both`。
