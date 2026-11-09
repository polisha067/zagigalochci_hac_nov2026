"""Consistent local SQLite backups using the SQLite backup API, including WAL data."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def backup_databases(directory, output):
    sources=sorted(Path(directory).glob('*.sqlite3'))
    if not sources:
        raise ValueError('No databases found; verify the persistent data directory')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    for source in sources:
        connection=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
        target=sqlite3.connect(output/source.name)
        try:
            connection.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Backup integrity check failed')
        finally:
            target.close();connection.close()
        (output/source.name).chmod(0o600)
    output.chmod(0o700)
    return len(sources)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=Path('/var/lib/transport'))
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or args.data/'backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    count=backup_databases(args.data,output)
    print(f'{count} database backups saved: {output}')


if __name__=='__main__':main()
