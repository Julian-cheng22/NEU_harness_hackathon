# 地图商家版数据集（maps_places/）

一个合成的地图商家/评价数据库（Google Maps 风格）。所有地点、商家和用户都是虚构的，与任何真实平台数据无关。时间为近期：2024-11-04 至 2025-03-08。harness 代码未做任何修改。用法和其他数据集完全相同。

## 目录内容

| 文件 | 说明 |
|---|---|
| `schema.sql` | 10 张表（类别、街区、商家、地点、用户、评价、照片、举报、广告、营业时间），MySQL 格式，故意不加外键 |
| `generate.py` | 固定随机种子生成 `seed.sql`；`--check` 验证结果是否一致 |
| `seed.sql` | 约 0.6 MB：308 个地点、500 名用户、6000 条评价、1500 张照片、150 条广告 |
| `defects.yaml` | 10 个缺陷（D1–D10），与原项目一一对应，`harness_tool` 沿用原来的 |
| `questions.yaml` | 31 道题：Q01–Q25 缺陷题，C01–C06 对照题，字段格式与原来一致 |

## 缺陷对应

| ID | 地图场景 |
|---|---|
| D1 | 地点 ID 命名不一致：`place_id` / `place_ref` / `poi_id` / `listing_id` |
| D2 | `places.status` 与 `ads.status` 同名不同取值域 |
| D3 | `places.review_count`、`places.avg_rating` 是过期缓存 |
| D4 | `places.closed_date` 为空有两种含义：仍在营业（或暂停），或永久关闭但没记日期 |
| D5 | `reviews.rating`、`places.seating_capacity` 是文本，混有 `N/A`、`unknown`、空串 |
| D6 | 同一家店（同名同地址）用不同 `place_id` 重复登记 |
| D7 | `source='MOBILE'` 的评价时间是 UTC（UTC = 本地 + 5 小时），WEB 为美东本地时间 |
| D8 | 评价的 `place_ref` 找不到地点（地点已被删除） |
| D9 | 举报原因代码文档只写了 1–4，实际有 5–9（遗留系统代码） |
| D10 | `billing_system='LEGACY'` 的广告花费单位是"分"，`ADS_V2` 是美元 |

数据窗口全部在美东标准时间内（夏令时 2024-11-03 结束、2025-03-09 开始），所以不需要处理夏令时。

## 已验证

在临时 MySQL 里导入 `seed.sql` 后，全部 31 道题的标准答案、25 道缺陷题的错误写法、10 个缺陷检测语句都能执行；10 个缺陷的检测结果均大于 0；除 Q21（只有标准答案）外，缺陷题的标准答案与错误写法结果都不同。`generate.py --check` 通过。

## 没有验证（需要确认）

和其他数据集一样：没有读 harness 代码，也没跑过 `eval/run.py`。请确认代码里有没有写死原来的表名或列名，Glossary 是否需要补 D7、D9、D10 的口径，以及 `learned_schema` 是否需要重新生成。

## 已知的小问题

- Q01、Q02、Q03 的错误写法会直接报"列不存在"，这是设计意图，考的是 `reviewer_id` / `poi_id` / `listing_id` 的命名陷阱。
- Q21 的答案只有 5，规模偏小。
- `places.owner_id` 为空表示"尚未被商家认领"，只有这一种含义，不是缺陷。

## 建议的跑法

路径请按实际项目结构调整。

```bash
docker compose down -v && docker compose up -d
docker compose exec -T mysql mysql -uroot -phackathon harness < maps_places/seed.sql
```

再把 `maps_places/questions.yaml`、`maps_places/defects.yaml` 复制成评测读取的 `data/` 下的同名文件（先备份原文件），然后跑 `eval/run.py --arm both`。
