"""Импорт скачанного CSV/ZIP без превращения статистики в тарифы."""
import argparse
import json
import sqlite3

from backend.app.config import Settings
from backend.network.observations import import_observations
from backend.network.asop import import_asop
from backend.network.references import import_references


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("kind",choices=["connectivity","regional","asop","context"])
    parser.add_argument("file")
    parser.add_argument("--database")
    args=parser.parse_args()
    target=args.database or str(Settings.from_env().database_path.parent/"network-observations.sqlite3")
    try:
        result=import_references(args.file,target) if args.kind == "context" else (import_asop(args.file,target) if args.kind == "asop" else import_observations(args.file,target,args.kind))
    except (ValueError,OSError,sqlite3.Error) as exc:
        parser.exit(1,str(exc)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
