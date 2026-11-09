"""Offline calculation with the reviewed ChukotAVIA snapshot."""
import argparse
import json
from pathlib import Path
from backend.network.chukotavia import calculate_chukotavia


def main():
    parser=argparse.ArgumentParser(description='Расчёт авиационного тарифа и плановых рейсов ЧукотАВИА')
    parser.add_argument('--request',type=Path,default=Path(__file__).parent/'examples/chukotavia-request.json')
    args=parser.parse_args()
    print(json.dumps(calculate_chukotavia(json.loads(args.request.read_text())),ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
