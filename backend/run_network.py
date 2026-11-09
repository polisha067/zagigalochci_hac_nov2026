import argparse
import json
from pathlib import Path
import sys

from backend.network.engine import calculate_network
from backend.network.graph import load_network


def main():
    parser=argparse.ArgumentParser(description="Расчёт сети с шестью физическими видами и мультимодальными цепочками")
    parser.add_argument("--request",type=Path,default=Path(__file__).resolve().parent/"examples/network-request.json")
    parser.add_argument("--network",type=Path,default=Path(__file__).resolve().parent/"examples/network.json")
    args=parser.parse_args()
    try:
        result=calculate_network(json.loads(args.request.read_text(encoding="utf-8")),load_network(args.network))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    sys.exit(main())
