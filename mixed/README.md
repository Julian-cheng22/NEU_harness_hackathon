# 五合一浓缩版数据集（mixed/）

把医疗、法律、电商、短视频、地图五个数据集浓缩成**一个数据库、30 道题**。数据全部由五个数据集原有的生成器生成（原样复制在 `sources/`），没有改动任何数据，所以缺陷和之前完全一致。harness 代码未做任何修改。

## 目录内容

| 文件 | 说明 |
|---|---|
| `generate.py` | 调用 `sources/` 里的五个生成器，只保留题目需要的表，加表名前缀后写出 `seed.sql`；`--check` 验证结果是否一致 |
| `sources/` | 五个数据集的 `generate.py` 和 `schema.sql` 原样副本（不含 `seed.sql`） |
| `seed.sql` | 约 2 MB，25 张表，导入后即可使用，包含建表语句 |
| `defects.yaml` | 10 个缺陷（D1–D10），每个缺陷合并了用到它的各行业实例，`detect_sql` 是各实例相加 |
| `questions.yaml` | 30 道题：Q01–Q25 缺陷题（每个行业 5 道），C01–C05 对照题（每个行业 1 道） |

## 表和前缀

同名的表（如 `users`、`reviews`、`status`）在不同行业里含义不同，所以每张表都加了前缀：

| 行业 | 前缀 | 保留的表 |
|---|---|---|
| 医疗 | `med_` | departments、admissions、lab_items、lab_events、prescriptions |
| 法律 | `leg_` | cases、parties、docket_entries、fee_invoices |
| 电商 | `ecom_` | warehouses、products、customers、orders、payments、shipments |
| 短视频 | `tt_` | creators、users、videos、likes、reports、promotions |
| 地图 | `map_` | owners、places、reviews、flags |

题目开头有行业标签，如 `【医疗】`，避免不同行业里"用户""评价"等词混淆。列名保持原样，只有表名加了前缀。

## 题目来源

| 题号 | 行业 | 来源 | 涉及缺陷 |
|---|---|---|---|
| Q01–Q05 | 医疗 | medical 的 Q01、Q04、Q05、Q07、Q10 | D1、D4、D5、D7、D10 |
| Q06–Q10 | 法律 | legal 的 Q04、Q06、Q13、Q19、Q20 | D2、D3、D6、D8、D9 |
| Q11–Q15 | 电商 | ecommerce 的 Q03、Q08、Q12、Q19、Q22 | D1、D4、D5、D8、D10 |
| Q16–Q20 | 短视频 | tiktok 的 Q05、Q06、Q13、Q20、Q23 | D2、D3、D6、D9、D10 |
| Q21–Q25 | 地图 | maps 的 Q03、Q08、Q10、Q16、Q19 | D1、D4、D5、D7、D8 |
| C01–C05 | 各 1 道 | 医疗 C02、法律 C04、电商 C01、短视频 C01、地图 C03 | 对照题 |

每道题的 `notes` 里也写了来源。10 个缺陷全部覆盖：D1、D4、D5、D8、D10 各 3 题，D2、D3、D6、D7、D9 各 2 题。

## 已验证

在临时 MySQL 里导入 `seed.sql` 后，30 道题的标准答案、25 道缺陷题的错误写法、10 个缺陷检测语句都能执行；10 个缺陷的检测结果均大于 0；缺陷题的标准答案与错误写法结果都不同，答案与各原数据集里对应题目的结果一致（例如 Q01 是 80）。`generate.py --check` 通过。

## 没有验证（需要确认）

和其他数据集一样：没有读 harness 代码，也没跑过 `eval/run.py`。特别要留意：
1. 数据库里现在有 25 张表，schema 比单个数据集长很多，小模型的上下文压力更大，这可能正是想要的效果。
2. 如果 harness 的 Glossary 要补口径，D7（医疗和地图的时间窗口、偏移不同）、D9、D10 需要按行业分别写。
3. 部分行业的同名概念在别的行业里含义不同（例如两个行业都有 `status`），Glossary 最好带表名前缀。

## 已知的小问题

- 这个版本浓缩的是题数和表数：从五个数据集各挑 6 道题，只保留这些题需要的 25 张表。数据量没有变小，约 2.5 万行，因为每张表都是原样保留。
- Q01、Q11、Q21 的错误写法会直接报"列不存在"，这是设计意图，考的是 ID 命名陷阱。
- D7 出现在医疗和地图两个行业，但两者的时间窗口不同：医疗是 2023-11-06 至 2024-03-09，地图是 2024-11-04 至 2025-03-08，各自都在美东标准时间内（UTC = 本地 + 5 小时）。

## 建议的跑法

路径请按实际项目结构调整。

```bash
docker compose down -v && docker compose up -d
docker compose exec -T mysql mysql -uroot -phackathon harness < mixed/seed.sql
```

再把 `mixed/questions.yaml`、`mixed/defects.yaml` 复制成评测读取的 `data/` 下的同名文件（先备份原文件），然后跑 `eval/run.py --arm both`。
