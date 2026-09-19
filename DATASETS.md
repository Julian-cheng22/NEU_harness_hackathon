# 数据集索引（五个行业，各 31 题）

每个目录都是一个独立的合成数据集，结构相同：`schema.sql`、`generate.py`、`seed.sql`、`defects.yaml`、`questions.yaml`、`README.md`。数据全部虚构，harness 代码未做任何修改。

| 目录 | 行业 | 说明 |
|---|---|---|
| `medical/` | 医疗 | 医院住院、化验、处方、理赔 |
| `legal/` | 法律 | 民事诉讼案件、docket、听证、律师费 |
| `ecommerce/` | 电商 | 订单、支付、发货、退货、评价 |
| `tiktok_feed/` | 短视频 | 视频推荐流（TikTok 风格）：展示、点赞、评论、推广 |
| `maps_places/` | 地图 | 地图商家（Google Maps 风格）：地点、评价、照片、广告 |

五合一的 30 题版本在单独的分支 `mixed-dataset` 上。

使用方法见各目录下的 `README.md`。
