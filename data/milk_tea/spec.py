"""Human-authored evaluation SQL and defect contracts; never imported by agents."""

INBOUND = """WITH unique_in AS (
  SELECT i.*, ROW_NUMBER() OVER (PARTITION BY movement_key ORDER BY row_id) AS rn
  FROM stock_in i
)"""
CUPS = "{a}.qty * CASE WHEN {a}.unit = 'box' THEN m.units_per_box ELSE 1 END"


def question_spec():
    questions = []

    def q(n, tags, text, zh, gold, naive=None, notes=""):
        value = dict(id=f"MTQ{n:02d}", defect_ids=tags, question=text, question_zh=zh,
                     answer_type="scalar", gold_sql=gold.strip(), notes=notes)
        if naive:
            value["naive_sql"] = naive.strip()
        questions.append(value)

    q(1, ["MT01"], "What is the approved sales quantity in base cups?",
      "已审核销售数量合计多少杯（基本单位）？",
      "SELECT SUM("+CUPS.format(a="s")+") AS n FROM sales s JOIN materials m USING(material_id) WHERE s.status='approved'",
      "SELECT SUM(qty) AS n FROM sales WHERE status='approved'")
    q(2, ["MT01"], "What is the posted stock-out quantity in base cups, including waste?",
      "已过账库存出库（含报损）合计多少杯？",
      "SELECT SUM("+CUPS.format(a="o")+") AS n FROM stock_out o JOIN materials m USING(material_id) WHERE o.status='posted'",
      "SELECT SUM(qty) AS n FROM stock_out WHERE status='posted'")
    q(3, ["MT02"], "How many distinct approved sales documents are there?",
      "已审核销售单有多少张（按单号去重）？",
      "SELECT COUNT(DISTINCT order_code) AS n FROM sales WHERE status='approved'",
      "SELECT COUNT(DISTINCT order_code) AS n FROM sales WHERE status<>'void'")
    q(4, ["MT02"], "How many approved sales-return document lines are there?",
      "已审核销售退货单有多少行？",
      "SELECT COUNT(*) AS n FROM sales_returns WHERE status='C'",
      "SELECT COUNT(*) AS n FROM sales_returns WHERE status='approved'",
      "Return ERP uses C=approved, A=draft. Sales uses approved/draft/void.")
    q(5, ["MT03"], "What is the approved refund total in CNY excluding tax, expressed as a positive amount?",
      "已审核退货退款未税金额合计多少元（退款以正数表示）？",
      "SELECT SUM(ABS(refund_net)) AS n FROM sales_returns WHERE status='C'",
      "SELECT SUM(refund_net) AS n FROM sales_returns WHERE status='C'")
    q(6, ["MT03"], "How many base cups were returned on approved sales returns, expressed as a positive quantity?",
      "已审核销售退货合计多少杯（退货数量以正数表示）？",
      "SELECT SUM(ABS(r.qty)*CASE WHEN r.unit='box' THEN m.units_per_box ELSE 1 END) AS n FROM sales_returns r JOIN materials m USING(material_id) WHERE r.status='C'",
      "SELECT SUM(r.qty*CASE WHEN r.unit='box' THEN m.units_per_box ELSE 1 END) AS n FROM sales_returns r JOIN materials m USING(material_id) WHERE r.status='C'")
    q(7, ["MT04"], "How many base cups physically entered inventory from posted purchases, counting each movement once?",
      "已过账采购入库实际增加多少杯库存（每个业务流水只计一次）？",
      INBOUND+" SELECT SUM("+CUPS.format(a="i")+") AS n FROM unique_in i JOIN materials m USING(material_id) WHERE rn=1 AND status='posted' AND source_type='purchase'",
      "SELECT SUM("+CUPS.format(a="i")+") AS n FROM stock_in i JOIN materials m USING(material_id) WHERE status='posted' AND source_type='purchase'")
    q(8, ["MT04"], "How many extra stock-in export rows are replays of an existing movement?",
      "库存入库导出表中有多少条重复导入的多余记录？",
      "SELECT COUNT(*)-COUNT(DISTINCT movement_key) AS n FROM stock_in",
      "SELECT COUNT(*)-COUNT(DISTINCT row_id) AS n FROM stock_in")
    q(9, ["MT05"], "What is the tax-inclusive total value of approved sales orders in CNY?",
      "已审核销售订单的价税合计是多少元？",
      "SELECT SUM(line_tax_amount) AS n FROM sales WHERE status='approved'",
      "SELECT SUM(order_total) AS n FROM sales WHERE status='approved'",
      "This is booked order value, not cash collected or accounting revenue. Two lines per order; header total repeated.")
    q(10, ["MT05"], "What is the largest approved sales order's tax-inclusive total in CNY? Return only the amount.",
      "金额最大的已审核销售订单价税合计是多少元？只返回金额。",
      "SELECT MAX(order_total) AS n FROM sales WHERE status='approved'",
      "SELECT SUM(order_total) AS n FROM sales WHERE status='approved' GROUP BY order_code ORDER BY n DESC LIMIT 1")
    q(11, ["MT06"], "How many base cups physically entered inventory from completed production? Count each physical receipt once.",
      "生产完成入库实际增加多少杯库存？每次实际入库只计一次。",
      "SELECT SUM("+CUPS.format(a="p")+") AS n FROM production_receipts p JOIN materials m USING(material_id) WHERE status='completed'",
      "SELECT SUM(n) AS n FROM (SELECT "+CUPS.format(a="p")+" AS n FROM production_receipts p JOIN materials m USING(material_id) WHERE status='completed' UNION ALL SELECT "+CUPS.format(a="i")+" AS n FROM stock_in i JOIN materials m USING(material_id) WHERE status='posted' AND source_type='production') x")
    q(12, ["MT06"], "What is the total physical inventory inflow in base cups from all posted sources, including production and returns? Count each movement once.",
      "所有已过账来源的实际入库合计多少杯（包括生产与退货，每个流水只计一次）？",
      INBOUND+" SELECT SUM("+CUPS.format(a="i")+") AS n FROM unique_in i JOIN materials m USING(material_id) WHERE rn=1 AND status='posted'",
      INBOUND+" SELECT SUM(n) AS n FROM (SELECT "+CUPS.format(a="i")+" AS n FROM unique_in i JOIN materials m USING(material_id) WHERE rn=1 AND status='posted' UNION ALL SELECT "+CUPS.format(a="p")+" AS n FROM production_receipts p JOIN materials m USING(material_id) WHERE status='completed') x")
    numeric = "weight_text REGEXP '^[0-9]+([.][0-9]+)?$'"
    q(13, ["MT07"], "What is the highest valid measured stock-out weight in kg across all export rows?",
      "全部出库导出记录中，最高的有效实测重量是多少 kg？",
      f"SELECT MAX(CAST(weight_text AS DECIMAL(18,4))) AS n FROM stock_out WHERE {numeric}",
      "SELECT MAX(weight_text) AS n FROM stock_out")
    q(14, ["MT07"], "What is the average valid measured stock-out weight in kg across all export rows, excluding unavailable readings? Round to 4 decimal places.",
      "全部出库记录的有效实测重量平均多少 kg（排除不可用读数，保留四位小数）？",
      f"SELECT ROUND(AVG(CAST(weight_text AS DECIMAL(18,4))),4) AS n FROM stock_out WHERE {numeric}",
      "SELECT ROUND(AVG(weight_text),4) AS n FROM stock_out")
    q(15, ["MT08"], "How many stock-in export rows are posted? This is a raw-row diagnostic; include replay rows.",
      "入库导出表有多少行已过账记录？本题统计原始行，包含重复导入。",
      "SELECT COUNT(*) AS n FROM stock_in WHERE status='posted'",
      "SELECT COUNT(*) AS n FROM stock_in WHERE posted_at IS NOT NULL")
    q(16, ["MT08"], "How many stock-in export rows are not posted? This is a raw-row diagnostic; include replay rows.",
      "入库导出表有多少行尚未过账？本题统计原始行，包含重复导入。",
      "SELECT COUNT(*) AS n FROM stock_in WHERE status<>'posted'",
      "SELECT COUNT(*) AS n FROM stock_in WHERE posted_at IS NULL")
    q(17, [], "How many material master records are there?", "物料主数据有多少条？", "SELECT COUNT(*) AS n FROM materials")
    q(18, [], "How many warehouses are in the warehouse master?", "仓库主数据有多少条？", "SELECT COUNT(*) AS n FROM warehouses")
    q(19, [], "How many customer stores are in the customer master?", "客户门店主数据有多少条？", "SELECT COUNT(*) AS n FROM customers")
    gold = INBOUND+" SELECT (SELECT SUM("+CUPS.format(a="i")+") FROM unique_in i JOIN materials m USING(material_id) WHERE rn=1 AND status='posted') - (SELECT SUM("+CUPS.format(a="o")+") FROM stock_out o JOIN materials m USING(material_id) WHERE status='posted') AS n"
    q(20, ["MT01", "MT02", "MT04", "MT06"], "As of 2026-09-19, what is inventory on hand in base cups across all warehouses? Opening inventory before these documents was zero.",
      "截至2026-09-19，全部仓库共有多少杯库存？单据之前期初库存为零。",
      gold, "SELECT (SELECT SUM(qty) FROM stock_in)+(SELECT SUM(qty) FROM production_receipts)-(SELECT SUM(qty) FROM stock_out) AS n",
      "Physical stock ledger only; sales orders are reservations and do not move inventory. Return and production stock-in mirrors must not be counted twice.")
    return questions


def defect_spec(evidence):
    specs = [
        ("MT01", "箱／杯混合计量单位", "Historical exports mix box and cup quantities; pack sizes vary by material.",
         "Convert qty with materials.units_per_box when unit='box'; base unit is cup.",
         "SUM(qty) adds boxes directly to cups.", "SELECT COUNT(*) AS evidence FROM sales WHERE unit='box'"),
        ("MT02", "跨系统状态口径不同", "Draft and void documents coexist with posted/approved business events.",
         "sales approved; sales_returns C; stock_in/out posted; production_receipts completed. Other states have no booked/stock effect.",
         "Reuse one status literal across tables or count draft documents.", "SELECT COUNT(*) AS evidence FROM sales WHERE status='draft'"),
        ("MT03", "旧系统退货使用负数", "The legacy return exporter uses signed credit quantities and amounts; ERP uses positive magnitudes.",
         "Both represent returns: ABS(qty) and ABS(refund_net), once per approved return.",
         "Signed and unsigned returns cancel each other when summed.", "SELECT COUNT(*) AS evidence FROM sales_returns WHERE qty<0"),
        ("MT04", "ETL重复导入", "40 purchase movements were replayed, acquiring fresh export row IDs.",
         "stock_in movement_key identifies a business event; replay payloads are identical. Keep the smallest row_id per movement_key.",
         "Distinct row_id cannot remove business duplicates.", "SELECT COUNT(*)-COUNT(DISTINCT movement_key) AS evidence FROM stock_in"),
        ("MT05", "表头金额重复摊在明细行", "Two sales lines per document each carry the same header total.",
         "SUM(line_tax_amount), or one order_total per order_code. SUM(DISTINCT order_total) is unsafe because unrelated orders can have equal amounts.",
         "SUM(order_total) over line-grain exports doubles the order value.", "SELECT COUNT(*)-COUNT(DISTINCT order_code) AS evidence FROM sales"),
        ("MT06", "生产入库与库存入库重复计数", "Every production receipt is mirrored into stock_in by source_line_id.",
         "stock_in is the physical inbound ledger including production and returns. Do not add production_receipts or sales_returns to that ledger.",
         "UNION ALL production receipts and stock-in counts the same receipt twice.", "SELECT COUNT(*) AS evidence FROM stock_in WHERE source_type='production'"),
        ("MT07", "重量读数混有文本占位符", "A scale import stores decimal kg readings as VARCHAR with N/A and empty strings.",
         "Exclude unavailable readings from AVG denominator; validate numeric syntax and CAST before MAX/AVG.",
         "MAX sorts strings; AVG converts junk to zero and retains it in the denominator.", "SELECT COUNT(*) AS evidence FROM stock_out WHERE weight_text IN ('N/A','')"),
        ("MT08", "缺失过账时间不代表未过账", "A webhook missed timestamps on some posted inbound records; drafts also have NULL timestamps.",
         "status determines posting, not posted_at. For physical movements additionally deduplicate movement_key.",
         "posted_at IS NULL incorrectly classifies some posted receipts as drafts.", "SELECT COUNT(*) AS evidence FROM stock_in WHERE status='posted' AND posted_at IS NULL"),
    ]
    return [dict(id=i, title=t, description=d, correct_rule=r, naive_failure=n,
                 evidence_sql=e, affected_export_rows=len(evidence[i]),
                 manifest_key=i, scope="Synthetic benchmark convention, not a claim about the source company")
            for i,t,d,r,n,e in specs]
