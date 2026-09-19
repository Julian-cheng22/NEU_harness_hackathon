# 法律案件版数据集（legal/）

一个合成的民事诉讼数据库，所有法院、法官、律所、当事人和案件都是虚构的。时间为近期：2024-11-04 至 2025-03-08。harness 代码未做任何修改。用法和 `medical/` 完全相同。

## 目录内容

| 文件 | 说明 |
|---|---|
| `schema.sql` | 10 张表（法院、法官、律所、律师、当事人、案件、案件当事人、docket、听证、律师费发票），MySQL 格式，故意不加外键 |
| `generate.py` | 固定随机种子生成 `seed.sql`；`--check` 验证结果是否一致 |
| `seed.sql` | 约 0.3 MB：300 个案件、2899 条 docket、464 次听证、350 张发票 |
| `defects.yaml` | 10 个缺陷（D1–D10），与原项目一一对应，`harness_tool` 沿用原来的 |
| `questions.yaml` | 31 道题：Q01–Q25 缺陷题，C01–C06 对照题，字段格式与原来一致 |

## 缺陷对应

| ID | 法律场景 |
|---|---|
| D1 | 案件 ID 命名不一致：`case_id` / `case_no` / `matter_id` |
| D2 | `cases.status` 与 `fee_invoices.status` 同名不同取值域 |
| D3 | `cases.num_filings` 是过期缓存，与 docket 实际条数不符 |
| D4 | `closed_date` 为空有两种含义：未结案，或保密和解（已结案但不记日期） |
| D5 | `cases.award_amount` 是文本，混有 `sealed`、`TBD`、`N/A`、空串 |
| D6 | 同一家公司用不同 `party_id` 重复登记 |
| D7 | `source='ECF'` 的 docket 时间是 UTC（UTC = 本地 + 5 小时），其余为美东本地时间 |
| D8 | docket 的 `case_no` 找不到对应案件 |
| D9 | 结案代码文档只写了 1–4，实际有 5–9（遗留系统代码） |
| D10 | 2025-01-15 之前开单的发票金额单位是"分"，之后是美元 |

数据窗口全部在美东标准时间内（夏令时 2024-11-03 结束、2025-03-09 开始），所以不需要处理夏令时。

## 已验证

在临时 MySQL 里导入 `seed.sql` 后，全部 31 道题的标准答案、25 道缺陷题的错误写法、10 个缺陷检测语句都能执行；10 个缺陷的检测结果均大于 0；除 Q21（只有标准答案）外，缺陷题的标准答案与错误写法结果都不同。`generate.py --check` 通过。

## 没有验证（需要确认）

和 `medical/` 一样：没有读 harness 代码，也没跑过 `eval/run.py`。请确认代码里有没有写死原来的表名或列名，Glossary 是否需要补 D7、D9、D10 的口径，以及 `learned_schema` 是否需要重新生成。

## 已知的小问题

- Q07 的差距很小（9.1367 对 9.1467），Q21 的答案只有 2。
- Q01、Q02、Q03 的错误写法会直接报"列不存在"，这是设计意图，考的是 `case_no` / `matter_id` 的命名陷阱。
- Q02、Q06 的"前 N 个案件"要求只统计存在的案件，因为孤儿 docket 记录（D8）也有案件号。

## 建议的跑法

路径请按实际项目结构调整。

```bash
docker compose down -v && docker compose up -d
docker compose exec -T mysql mysql -uroot -phackathon harness < legal/seed.sql
```

再把 `legal/questions.yaml`、`legal/defects.yaml` 复制成评测读取的 `data/` 下的同名文件（先备份原文件），然后跑 `eval/run.py --arm both`。
