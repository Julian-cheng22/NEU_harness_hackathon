"""
Harness: tools that let a small model reason correctly over messy internal data.

The thesis of this project is that most text-to-SQL failure on real company
data is not a SQL-syntax problem -- it is a CONTEXT problem. The model writes
valid SQL against the schema it was shown; the schema just does not say that
`plan_tier` is stale, that `event_value` is full of 'N/A', or that `acct_id`
is the customer key. So the model is confidently, silently wrong.

Each module here closes one of those gaps:

    db           read-only execution with a repair-friendly error path
    schema_card  M-Schema rendering (+ the raw-DDL baseline for comparison)
    joins        infer the join graph from value overlap, not column names
    profile      data-quality probe: type drift, stale caches, ambiguous NULLs
    glossary     business-term resolution (units, tax, timezone conventions)
    execute      validate -> run -> repair loop
    agent        the orchestrator that ties the tools together

Defect ids referenced in docstrings (D1..D10) are defined in data/defects.yaml.
"""

__all__ = ["db", "schema_card", "joins", "profile"]
__version__ = "0.1.0"
