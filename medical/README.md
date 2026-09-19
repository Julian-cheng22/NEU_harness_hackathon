# 医疗版数据集（medical/）

一个合成的医院数据库，用来替换原来的 B2B SaaS 数据。harness 代码未做任何修改。

## 目录内容

| 文件 | 说明 |
|---|---|
| `schema.sql` | 10 张表（患者、住院、化验、处方、理赔等），MySQL 格式，故意不加外键 |
| `generate.py` | 固定随机种子生成 `seed.sql`；`--check` 验证结果是否一致 |
| `seed.sql` | 约 0.8 MB：128 名患者、420 次住院、约 6800 条化验、836 条处方、387 条理赔 |
| `defects.yaml` | 10 个缺陷（D1–D10），与原项目一一对应，`harness_tool` 沿用原来的 |
| `questions.yaml` | 31 道题：Q01–Q25 缺陷题，C01–C06 对照题，字段格式与原来一致 |

## 缺陷对应

| ID | 医疗场景 |
|---|---|
| D1 | 患者 ID 命名不一致：`subject_id` / `pat_id` / `patient_id` |
| D2 | `admissions.status` 与 `claims.status` 同名不同取值域 |
| D3 | `patients.total_admissions`、`last_dept_id` 是过期缓存 |
| D4 | `dischtime` 为空有两种含义：仍在住院，或已死亡但缺记录 |
| D5 | `lab_events.value` 是文本，混有 `NEG`、`pending`、`>100` 等 |
| D6 | 同一患者用不同 ID 重复登记 |
| D7 | `source='LIS_BATCH'` 的化验时间是 UTC（UTC = 本地 + 5 小时），其余为美东本地时间 |
| D8 | 处方的 `hadm_id` 找不到对应住院记录 |
| D9 | 出院代码文档只写了 1–4，实际有 5–9（遗留系统代码） |
| D10 | 血糖 mg/dL 与 mmol/L 混用；2024-01-15 之前的理赔金额单位是"分" |

数据时间窗口为 2023-11-06 至 2024-03-09，全部在美东标准时间内，所以不需要处理夏令时。

## 已验证

在临时 MySQL 里导入 `seed.sql` 后，全部 31 道题的标准答案、25 道缺陷题的错误写法、10 个缺陷检测语句都能执行；10 个缺陷的检测结果均大于 0；缺陷题的标准答案与错误写法结果都不同。`generate.py --check` 通过。

## 没有验证（需要确认）

我没有读 harness 代码，也没跑过 `eval/run.py`：

1. 代码里有没有写死原来的表名或列名（`customers`、`invoices` 等）。
2. Glossary 是否写死了原来的业务口径。医疗版需要补：D7 的时区规则、D9 的遗留代码、D10 的金额单位切换日。
3. `data/learned_schema.yaml/.md` 是按原数据学出来的，换数据后应重新生成。

## 建议的跑法

路径请按实际项目结构调整。

1. 先清空原来的库。`seed.sql` 只会删除它自己建的 10 张表，原来的表会残留，可能干扰 join 推断：
   ```bash
   docker compose down -v && docker compose up -d
   docker compose exec -T mysql mysql -uroot -phackathon harness < medical/seed.sql
   ```
2. 让评测读到新的题目和缺陷文件。原项目大概率读 `data/questions.yaml` 和 `data/defects.yaml`：先备份这两个文件，再把 `medical/` 里的同名文件复制过去。
3. 先跑 `pytest tests/ -q`。测试里若有针对原数据的断言，会失败，这是预期的，需要判断哪些要改。
4. 再跑 `eval/run.py --arm both`。

## 已知的小问题

- 个别题的差距很小：Q14 是 16 对 15，Q15 是 1 对 4。
- Q01、Q11、Q12 的错误写法会直接报"列不存在"，这是设计意图，考的是 `pat_id` / `patient_id` 的命名陷阱。
