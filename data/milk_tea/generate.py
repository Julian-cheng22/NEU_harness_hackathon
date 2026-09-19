"""Generate exactly 10,000 synthetic rows and an independent Python oracle.

python -m data.milk_tea.generate [--check]
Generated artifacts are frozen together. No database or model is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DATASET_VERSION = "milk-tea-1.0.0"
SEED = 20260919
D = Decimal
COUNTS = {"materials": 20, "warehouses": 4, "customers": 26,
          "sales": 4000, "sales_returns": 600, "stock_in": 2200,
          "stock_out": 2200, "production_receipts": 950}

# Field names inspired by generic supply-chain document exports. Values and
# all business conventions below are invented for this benchmark.
COMMON = [
    ("row_id", "BIGINT PRIMARY KEY", "Export row identifier"),
    ("order_code", "VARCHAR(30) NOT NULL", "单据编号 / document number"),
    ("line_no", "INT NOT NULL", "单据行号 / line number"),
    ("order_date", "DATE NOT NULL", "业务日期 / business date"),
    ("status", "VARCHAR(16) NOT NULL", "单据状态 / document status"),
    ("material_id", "INT NOT NULL", "物料编码 / material key"),
    ("warehouse_id", "INT NOT NULL", "仓库编码 / warehouse key"),
    ("unit", "VARCHAR(12) NOT NULL", "单据单位 / document unit"),
    ("qty", "DECIMAL(18,3) NOT NULL", "单据数量 / document quantity"),
    ("batch_no", "VARCHAR(24) NOT NULL", "批号 / batch number"),
]
FIELDS = {
    "materials": [("material_id", "INT PRIMARY KEY", "物料编码"),
                  ("material_name", "VARCHAR(64) NOT NULL", "物料名称"),
                  ("base_unit", "VARCHAR(12) NOT NULL", "基本单位"),
                  ("units_per_box", "INT NOT NULL", "箱规"),
                  ("shelf_life_days", "INT NOT NULL", "保质期天数")],
    "warehouses": [("warehouse_id", "INT PRIMARY KEY", "仓库编码"),
                   ("warehouse_name", "VARCHAR(64) NOT NULL", "仓库名称")],
    "customers": [("customer_id", "INT PRIMARY KEY", "客户编码"),
                  ("customer_name", "VARCHAR(64) NOT NULL", "客户名称")],
    "sales": COMMON + [
        ("customer_id", "INT NOT NULL", "客户编码"),
        ("line_net_amount", "DECIMAL(18,2) NOT NULL", "行未税金额 CNY"),
        ("line_tax_amount", "DECIMAL(18,2) NOT NULL", "行价税合计 CNY"),
        ("order_total", "DECIMAL(18,2) NOT NULL", "订单价税合计 CNY")],
    "sales_returns": COMMON + [
        ("sale_line_id", "BIGINT NOT NULL", "原销售行标识"),
        ("source_system", "VARCHAR(16) NOT NULL", "来源系统"),
        ("refund_net", "DECIMAL(18,2) NOT NULL", "退货未税金额 CNY")],
    "stock_in": COMMON + [
        ("movement_key", "VARCHAR(32) NOT NULL", "业务流水标识"),
        ("source_type", "VARCHAR(20) NOT NULL", "来源单据类型"),
        ("source_line_id", "BIGINT", "来源单据行标识"),
        ("posted_at", "DATETIME", "过账时间")],
    "stock_out": COMMON + [
        ("source_type", "VARCHAR(20) NOT NULL", "出库类型"),
        ("sale_line_id", "BIGINT", "来源销售行标识"),
        ("weight_text", "VARCHAR(20)", "实测重量 kg")],
    "production_receipts": COMMON + [
        ("production_order", "VARCHAR(30) NOT NULL", "生产工单"),
        ("prd_date", "DATE NOT NULL", "生产日期"),
        ("exp_date", "DATE NOT NULL", "有效期至")],
}


def money(value):
    return D(value).quantize(D("0.01"))


def build():
    rng = random.Random(SEED)
    tables = {t: [] for t in COUNTS}
    for i in range(20):
        tables["materials"].append(dict(material_id=1001+i,
            material_name=f"模拟奶茶基底-{i+1:02d}", base_unit="cup",
            units_per_box=[6, 12, 24, 48][i % 4], shelf_life_days=30))
    for i in range(4):
        tables["warehouses"].append(dict(warehouse_id=2001+i, warehouse_name=f"虚构配送仓-{i+1}"))
    for i in range(26):
        tables["customers"].append(dict(customer_id=3001+i, customer_name=f"虚构奶茶门店-{i+1:02d}"))
    materials = {m["material_id"]: m for m in tables["materials"]}
    evidence = {f"MT{i:02d}": [] for i in range(1, 9)}

    def base(table, i, day, status, qty=None):
        mid = 1001 + rng.randrange(20)
        unit = "box" if i % 5 == 0 else "cup"
        row = dict(row_id={"sales":100000, "sales_returns":200000,
            "stock_in":300000, "stock_out":400000, "production_receipts":500000}[table]+i+1,
            order_code={"sales":"SO", "sales_returns":"SR", "stock_in":"IN",
                "stock_out":"OUT", "production_receipts":"PR"}[table]+f"-{i+1:06d}",
            line_no=1, order_date=day, status=status, material_id=mid,
            warehouse_id=2001+rng.randrange(4), unit=unit,
            qty=D(qty if qty is not None else rng.randint(2, 12)), batch_no=f"B-{mid}-0901")
        return row

    def cups(row):
        return abs(row["qty"]) * (materials[row["material_id"]]["units_per_box"] if row["unit"] == "box" else 1)

    # Business truth comes first. Defects affect export representation only.
    for i in range(4000):
        doc = i // 2
        status = "void" if doc % 10 == 0 else ("draft" if doc % 10 == 1 else "approved")
        s = base("sales", i, date(2026, 9, 10)+timedelta(days=doc % 5), status)
        s.update(order_code=f"SO-{doc+1:06d}", line_no=i % 2+1,
                 customer_id=3001+doc % 26)
        # CNY fixed per-base-unit catalog price, no float arithmetic.
        s["line_net_amount"] = money(cups(s) * D(5 + (s["material_id"] % 7)))
        s["line_tax_amount"] = money(s["line_net_amount"] * D("1.06"))
        tables["sales"].append(s)
    for i in range(0, 4000, 2):
        pair = tables["sales"][i:i+2]
        total = sum(s["line_tax_amount"] for s in pair)
        for s in pair:
            s["order_total"] = total
            evidence["MT05"].append(s["row_id"])

    # Every production record has exactly one inventory posting (or draft
    # mirror). These are two records describing ONE physical receipt.
    for i in range(950):
        p = base("production_receipts", i, date(2026, 9, 5),
                 "completed" if i % 10 else "draft", qty=200)
        p.update(production_order=f"WO-{i+1:06d}", prd_date=date(2026, 9, 5), exp_date=date(2026, 10, 5),
                 batch_no=f"P-{i+1:06d}")
        tables["production_receipts"].append(p)

    approved = [s for s in tables["sales"] if s["status"] == "approved"]
    for i in range(600):
        s = approved[i]
        r = base("sales_returns", i, date(2026, 9, 18), "C" if i % 10 else "A", qty=1)
        for key in ("material_id", "warehouse_id", "unit", "batch_no"):
            r[key] = s[key]
        r.update(sale_line_id=s["row_id"], source_system="legacy" if i % 4 == 0 else "erp",
                 refund_net=money(cups(r) * D(5+s["material_id"] % 7)))
        if r["source_system"] == "legacy":
            r["qty"] *= -1
            r["refund_net"] *= -1
            evidence["MT03"].append(r["row_id"])
        tables["sales_returns"].append(r)

    for i in range(2160):
        s = base("stock_in", i, date(2026, 9, 1), "posted", qty=1000)
        s.update(movement_key=f"MOV-{i+1:06d}", source_type="purchase", source_line_id=None)
        source = None
        if i < 950:
            source = tables["production_receipts"][i]
            s.update(source_type="production", status="posted" if source["status"] == "completed" else "draft")
            evidence["MT06"].append(s["row_id"])
        elif i < 1550:
            source = tables["sales_returns"][i-950]
            s.update(source_type="sales_return", status="posted" if source["status"] == "C" else "draft")
        if source:
            for key in ("material_id", "warehouse_id", "unit", "batch_no", "order_date"):
                s[key] = source[key]
            s.update(source_line_id=source["row_id"], qty=abs(source["qty"]))
        else:
            # Opening purchase: every material x warehouse starts with 10,000
            # cups, so unrelated negative stock cannot contaminate our traps.
            j = i-1550
            s.update(material_id=1001+j % 20, warehouse_id=2001+(j // 20) % 4,
                     unit="cup", qty=D(10000), batch_no=f"B-{1001+j%20}-0901")
            if j >= 80 and j % 11 == 0:
                s["status"] = "draft"
        s["posted_at"] = str(s["order_date"])+" 12:00:00" if s["status"] == "posted" else None
        if s["status"] == "posted" and i % 13 == 0:
            s["posted_at"] = None
            evidence["MT08"].append(s["row_id"])
        tables["stock_in"].append(s)
    # Append-only ETL replay: same business movement, different export row ID.
    for i in range(40):
        s = dict(tables["stock_in"][1550+i])
        s["row_id"] = 302161+i
        tables["stock_in"].append(s)
        evidence["MT04"].append(s["row_id"])

    weights = []
    for i in range(2200):
        s = base("stock_out", i, date(2026, 9, 16), "posted" if i < 600 or i % 11 else "draft", qty=1)
        if i < 2000:
            sale = approved[i]
            for key in ("material_id", "warehouse_id", "unit", "qty", "batch_no"):
                s[key] = sale[key]
            s.update(source_type="sale", sale_line_id=sale["row_id"])
        else:
            s.update(source_type="waste", sale_line_id=None, unit="cup")
        weight = D(rng.randrange(100, 50000)) / 100
        if i % 17 == 0:
            s["weight_text"] = "N/A" if i % 2 else ""
            evidence["MT07"].append(s["row_id"])
        else:
            s["weight_text"] = f"{weight:.2f}"
            weights.append(weight)
        tables["stock_out"].append(s)

    for table in list(COUNTS)[3:]:
        valid_status = {"sales":"approved", "sales_returns":"C", "stock_in":"posted",
                        "stock_out":"posted", "production_receipts":"completed"}[table]
        for row in tables[table]:
            if row["unit"] == "box":
                evidence["MT01"].append(row["row_id"])
            if row["status"] != valid_status:
                evidence["MT02"].append(row["row_id"])

    # Independent oracle: business events and integer/Decimal arithmetic, no
    # SQL execution and no parsing of gold queries. Not loaded into agent DB.
    sales = [s for s in tables["sales"] if s["status"] == "approved"]
    returns = [r for r in tables["sales_returns"] if r["status"] == "C"]
    inbound = [r for r in tables["stock_in"][:2160] if r["status"] == "posted"]
    outbound = [r for r in tables["stock_out"] if r["status"] == "posted"]
    production = [r for r in tables["production_receipts"] if r["status"] == "completed"]
    raw_posted = [r for r in tables["stock_in"] if r["status"] == "posted"]
    oracle = {
        "MTQ01": sum(cups(r) for r in sales),
        "MTQ02": sum(cups(r) for r in outbound),
        "MTQ03": len({r["order_code"] for r in sales}),
        "MTQ04": len(returns),
        "MTQ05": sum(abs(r["refund_net"]) for r in returns),
        "MTQ06": sum(cups(r) for r in returns),
        "MTQ07": sum(cups(r) for r in inbound if r["source_type"] == "purchase"),
        "MTQ08": len(tables["stock_in"]) - len({r["movement_key"] for r in tables["stock_in"]}),
        "MTQ09": sum(r["line_tax_amount"] for r in sales),
        "MTQ10": max(r["order_total"] for r in sales),
        "MTQ11": sum(cups(r) for r in production),
        "MTQ12": sum(cups(r) for r in inbound),
        "MTQ13": max(weights),
        "MTQ14": (sum(weights)/len(weights)).quantize(D("0.0001")),
        "MTQ15": len(raw_posted),
        "MTQ16": len(tables["stock_in"]) - len(raw_posted),
        "MTQ17": 20, "MTQ18": 4, "MTQ19": 26,
        "MTQ20": sum(cups(r) for r in inbound)-sum(cups(r) for r in outbound),
    }
    evidence["MT08"] = [r["row_id"] for r in raw_posted if r["posted_at"] is None]
    assert {t: len(rows) for t, rows in tables.items()} == COUNTS
    assert sum(COUNTS.values()) == 10000
    return tables, oracle, evidence


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, Decimal)):
        return str(value)
    return "'"+str(value).replace("'", "''")+"'"


def schema_sql():
    parts = ["-- Synthetic milk-tea benchmark; MySQL 8.0.35. Load into an EMPTY dedicated schema.\n"]
    for table, fields in FIELDS.items():
        cols = [f"  `{name}` {dtype} COMMENT {literal(comment)}" for name, dtype, comment in fields]
        if table in COUNTS and table not in {"materials", "warehouses", "customers"}:
            cols += ["  INDEX ix_material (material_id)", "  INDEX ix_warehouse (warehouse_id)",
                     "  INDEX ix_order (order_code)", "  INDEX ix_status (status)"]
        if table == "stock_in":
            cols += ["  INDEX ix_movement (movement_key)", "  INDEX ix_source (source_type, source_line_id)"]
        if table in {"stock_out", "sales_returns"}:
            cols += ["  INDEX ix_sale (sale_line_id)"]
        parts.append(f"CREATE TABLE `{table}` (\n"+",\n".join(cols)+"\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    return "\n".join(parts)


def artifacts():
    from .spec import question_spec, defect_spec
    tables, oracle, evidence = build()
    seed = [f"-- Generated synthetic dataset {DATASET_VERSION}; seed={SEED}; exactly 10000 rows.\nSET NAMES utf8mb4;\n"]
    for table, rows in tables.items():
        columns = [f[0] for f in FIELDS[table]]
        for offset in range(0, len(rows), 250):
            seed.append(f"INSERT INTO `{table}` ("+", ".join(f"`{c}`" for c in columns)+") VALUES\n"+
                        ",\n".join("("+", ".join(literal(row[c]) for c in columns)+")" for row in rows[offset:offset+250])+";\n")
    qs = question_spec()
    for q in qs:
        # YAML numeric scalars; all money is within exact cent precision.
        value = oracle[q["id"]]
        q["expect"] = float(value) if isinstance(value, Decimal) else value
    dump = lambda x: yaml.safe_dump(x, allow_unicode=True, sort_keys=False, width=110)
    result = {"schema.sql": schema_sql(), "seed.sql": "\n".join(seed),
        "questions.yaml": dump({"meta":{"dataset_version":DATASET_VERSION, "grading":"execution_match",
                                 "compare":"row_multiset", "as_of":"2026-09-19"}, "questions":qs}),
        "defects.yaml": dump({"dataset_version":DATASET_VERSION, "defects":defect_spec(evidence)}),
        "injection_manifest.json": json.dumps({"dataset_version":DATASET_VERSION, "seed":SEED,
             "affected_row_ids":evidence}, indent=2)+"\n"}
    report = {"dataset_version":DATASET_VERSION, "seed":SEED, "total_rows":10000,
        "rows_per_table": COUNTS, "questions":len(qs), "controls":sum(not q["defect_ids"] for q in qs),
        "affected_export_rows":{k:len(v) for k,v in evidence.items()},
        "sha256":{k:hashlib.sha256(v.encode()).hexdigest() for k,v in result.items()}}
    result["manifest.json"] = json.dumps(report, indent=2)+"\n"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift = []
    for filename, content in artifacts().items():
        path = HERE / filename
        if args.check:
            if not path.exists() or path.read_bytes() != content.encode("utf-8"):
                drift.append(filename)
        else:
            path.write_text(content, encoding="utf-8")
    if drift:
        print("DRIFT: "+", ".join(drift))
        return 1
    print(f"{'Verified' if args.check else 'Generated'} {DATASET_VERSION}: 10000 rows, 8 defects, 20 questions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
