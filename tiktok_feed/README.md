# 短视频推流版数据集（tiktok_feed/）

一个合成的短视频推荐流数据库（TikTok 风格）。所有创作者、用户和视频都是虚构的，与任何真实平台数据无关。时间为近期：2024-11-04 至 2025-03-08。harness 代码未做任何修改。用法和 `medical/`、`legal/`、`ecommerce/` 完全相同。

## 目录内容

| 文件 | 说明 |
|---|---|
| `schema.sql` | 10 张表（配乐、创作者、用户、视频、展示、点赞、评论、关注、举报、推广），MySQL 格式，故意不加外键 |
| `generate.py` | 固定随机种子生成 `seed.sql`；`--check` 验证结果是否一致 |
| `seed.sql` | 约 1.4 MB：408 名用户、500 个视频、12000 条展示、5000 条点赞、2500 条评论 |
| `defects.yaml` | 10 个缺陷（D1–D10），与原项目一一对应，`harness_tool` 沿用原来的 |
| `questions.yaml` | 31 道题：Q01–Q25 缺陷题，C01–C06 对照题，字段格式与原来一致 |

## 缺陷对应

| ID | 短视频场景 |
|---|---|
| D1 | 用户 ID 命名不一致：`user_id` / `uid` / `viewer_id` / `commenter_id` / `follower_id` |
| D2 | `videos.status` 与 `promotions.status` 同名不同取值域 |
| D3 | `videos.like_count`、`creators.follower_count` 是过期缓存 |
| D4 | `videos.removed_at` 为空有两种含义：视频仍在，或旧系统下架但没记时间 |
| D5 | `videos.duration_sec` 是文本，混有 `N/A`、`live`、空串 |
| D6 | 同一设备（`device_id`）注册了多个账号 |
| D7 | `impressions.source='CLIENT'` 的时间是 UTC（UTC = 本地 + 5 小时），SERVER 为美东本地时间 |
| D8 | 点赞的 `video_id` 找不到视频（视频已彻底删除） |
| D9 | 举报原因代码文档只写了 1–4，实际有 5–9（遗留系统代码） |
| D10 | `billing_system='LEGACY'` 的推广预算单位是"分"，`ADS_V2` 是美元 |

数据窗口全部在美东标准时间内（夏令时 2024-11-03 结束、2025-03-09 开始），所以不需要处理夏令时。

## 已验证

在临时 MySQL 里导入 `seed.sql` 后，全部 31 道题的标准答案、25 道缺陷题的错误写法、10 个缺陷检测语句都能执行；10 个缺陷的检测结果均大于 0；除 Q21（只有标准答案）外，缺陷题的标准答案与错误写法结果都不同。`generate.py --check` 通过。

## 没有验证（需要确认）

和其他数据集一样：没有读 harness 代码，也没跑过 `eval/run.py`。请确认代码里有没有写死原来的表名或列名，Glossary 是否需要补 D7、D9、D10 的口径，以及 `learned_schema` 是否需要重新生成。

## 已知的小问题

- Q01、Q02、Q03 的错误写法会直接报"列不存在"，这是设计意图，考的是 `viewer_id` / `commenter_id` / `uid` 的命名陷阱。
- Q21 的答案只有 6，Q05 的答案是 53，规模偏小。
- 这个数据集最大（12000 条展示），导入比其他几个慢一点。

## 建议的跑法

路径请按实际项目结构调整。

```bash
docker compose down -v && docker compose up -d
docker compose exec -T mysql mysql -uroot -phackathon harness < tiktok_feed/seed.sql
```

再把 `tiktok_feed/questions.yaml`、`tiktok_feed/defects.yaml` 复制成评测读取的 `data/` 下的同名文件（先备份原文件），然后跑 `eval/run.py --arm both`。
