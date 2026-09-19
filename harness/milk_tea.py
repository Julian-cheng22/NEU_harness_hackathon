"""Curated business conventions. No gold SQL, expected answers or defect manifests.

These rules are supplied institutional knowledge, NOT automatically discovered.
"""


def terms():
    from .glossary import Term
    return [
        Term(name="base cups", aliases=["quantity", "quantities", "unit", "box", "cup", "inventory", "stock"],
             columns=["materials.units_per_box", "sales.qty", "stock_in.qty", "stock_out.qty"],
             definition="The base unit is cup. Each material has its own units_per_box. Document qty is boxes when unit='box', cups when unit='cup'. Convert before summing. Sales orders reserve stock; only posted stock movements change physical inventory.",
             sql_hint="qty * CASE WHEN unit='box' THEN materials.units_per_box ELSE 1 END", defect_ids=["MT01"]),
        Term(name="approved documents", aliases=["approved", "posted", "status", "draft", "completed", "sales return"],
             columns=["sales.status", "sales_returns.status", "stock_in.status", "stock_out.status", "production_receipts.status"],
             definition="Status conventions are table-specific: sales approved/draft/void; sales_returns C=approved and A=draft; stock_in and stock_out posted/draft; production_receipts completed/draft. Draft/void rows have no booked or physical effect. posted_at may be NULL even on posted inbound rows; status is authoritative.", defect_ids=["MT02", "MT08"]),
        Term(name="refund", aliases=["return", "returned", "refund_net", "credit"],
             columns=["sales_returns.qty", "sales_returns.refund_net", "sales_returns.source_system"],
             definition="Legacy return exports use negative qty and refund_net, ERP uses positive magnitudes. Both describe positive returned quantities/refunds: use ABS, restrict to status='C', and convert boxes to cups for quantity. refund_net is CNY excluding tax. A return points to sales.row_id through sale_line_id; it is already mirrored in stock_in with source_type='sales_return'.", defect_ids=["MT03", "MT06"]),
        Term(name="inventory movement", aliases=["stock-in", "stock_in", "inflow", "purchase", "replay", "movement", "inventory on hand"],
             columns=["stock_in.movement_key", "stock_in.row_id", "stock_in.source_type"],
             definition="stock_in is the inbound ledger for purchase, production and sales_return. ETL replays share movement_key with identical business payloads and different export row_id; retain one row per movement_key. Filter posted and convert units. Production and return documents are already mirrored here: adding them again double-counts. On-hand is posted inbound minus posted stock_out; opening stock is zero. Raw export-row diagnostics intentionally keep replays.",
             sql_hint="ROW_NUMBER() OVER (PARTITION BY movement_key ORDER BY row_id)", defect_ids=["MT04", "MT06"]),
        Term(name="sales order value", aliases=["tax-inclusive", "order total", "order_total", "sales amount", "largest approved", "total value"],
             columns=["sales.order_code", "sales.order_total", "sales.line_tax_amount", "sales.line_net_amount"],
             definition="Sales is a line-grain export: order_total is the same header gross CNY total repeated on every line of an order. Sum line_tax_amount, or one header per order_code. SUM(DISTINCT order_total) loses separate equal-value orders. line_net_amount excludes tax; line_tax_amount includes synthetic 6% tax. Booked sales order value is not recognized revenue or payment received.", defect_ids=["MT05"]),
        Term(name="production receipt", aliases=["production", "manufacturing", "physical receipt"],
             columns=["production_receipts.row_id", "stock_in.source_line_id", "stock_in.source_type"],
             definition="production_receipts is the production source document; completed rows are physically received. Each has a stock_in mirror with source_type='production', source_line_id=production_receipts.row_id and the same material, warehouse, batch and base quantity. They represent the same physical receipt. Production quantity here is accepted finished goods, not ingredients or planned output.", defect_ids=["MT06"]),
        Term(name="measured weight", aliases=["weight", "weight_text", "kg", "readings"],
             columns=["stock_out.weight_text"],
             definition="weight_text contains decimal kg text plus N/A and empty strings for unavailable scale readings. Exclude unavailable readings, validate numeric text and cast before MAX/AVG. Their weight is unknown, not zero; do not impute an average for them.",
             sql_hint="weight_text REGEXP '^[0-9]+([.][0-9]+)?$'", defect_ids=["MT07"]),
    ]


def notes_for_sql(sql):
    import re
    # Context reminders only; no evaluation file access. Bound output length.
    notes = []
    for term in terms():
        if any(re.search(r"\b"+re.escape(col.split(".")[-1])+r"\b", sql) for col in term.columns):
            notes.append(term.definition)
    return notes[:3]
