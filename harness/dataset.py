"""Select a benchmark without mixing its database, questions or results."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def name() -> str:
    value = os.getenv("HARNESS_DATASET", "saas").strip().lower()
    if value not in {"saas", "milk_tea"}:
        raise ValueError(f"Unknown HARNESS_DATASET: {value!r}")
    return value


def data_dir() -> Path:
    return ROOT / "data" if name() == "saas" else ROOT / "data" / "milk_tea"


def results_dir() -> Path:
    return ROOT / "eval" / "out" if name() == "saas" else ROOT / "eval" / "out" / name()


def database_name() -> str:
    return "harness" if name() == "saas" else "milk_tea"
