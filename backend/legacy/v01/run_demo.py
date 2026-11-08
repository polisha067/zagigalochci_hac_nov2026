"""Запуск из корня репозитория: python -m backend.legacy.v01.run_demo."""

import argparse
import json
from pathlib import Path
import sys

from backend.legacy.v01.calculator import compare_delivery


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Пробный расчёт регулярных перевозок")
    parser.add_argument("--request", type=Path, default=root / "examples/request.json")
    parser.add_argument("--dataset", type=Path, default=root / "examples/dataset.json")
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or not isinstance(dataset, dict):
            raise ValueError("Запрос и набор данных должны быть JSON-объектами")
        print(json.dumps(compare_delivery(request, dataset), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
