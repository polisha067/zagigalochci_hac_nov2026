"""python -m backend.run_russia backend/examples/russia-request.json"""
import argparse
import json
from pathlib import Path
from backend.network.russia import load_russia

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('request',type=Path);args=parser.parse_args()
    print(json.dumps(load_russia().calculate(json.loads(args.request.read_text())),ensure_ascii=False,indent=2))
