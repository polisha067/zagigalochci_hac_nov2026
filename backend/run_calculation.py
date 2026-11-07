"""Расчёт без сервера: python -m backend.run_calculation [--request путь.json]."""

import argparse
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from backend.transport_calculator import compare_transport, load_catalog


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Расчёт перевозок версии 0.2")
    parser.add_argument("--request", type=Path, default=root / "examples/calculation-request.json")
    parser.add_argument("--catalog", type=Path, default=root / "examples/catalog.json")
    args = parser.parse_args()
    try:
        result = compare_transport(json.loads(args.request.read_text(encoding="utf-8")), load_catalog(args.catalog))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValidationError, ValueError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
