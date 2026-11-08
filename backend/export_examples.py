"""Regenerate derived examples/CSV locally; canonical data stays in Git.

python -m backend.export_examples [--kind all|examples|asop] [--output DIR]
No network, mail, authentication or database writes.
"""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_FIELDS = ('source_filename source_sha256 page metric category_id category_name year '
              'period_kind period_start period_end_exclusive reference_role source_value '
              'source_unit value_tonnes source_decimal_places rounding_half_step_source quality_flags').split()


def export_asop(output):
    from backend.network.asop import load_snapshot
    snapshot = load_snapshot()
    sources = {row['id']: row for row in snapshot['source_files']}
    rows = []
    for observation in snapshot['observations']:
        source = sources[observation['source_file_id']]
        row = {key: observation.get(key) for key in CSV_FIELDS}
        row.update(source_filename=source['filename'], source_sha256=source['sha256'],
                   quality_flags='|'.join(observation['quality_flags']))
        rows.append(row)
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for name, period in (('observations', None), ('annual', 'year'), ('quarterly', 'Q1')):
        path = output / (name + '.csv')
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, delimiter=';')
            writer.writeheader()
            writer.writerows(row for row in rows if period is None or row['period_kind'] == period)
        written.append(path)
    return written


def export_calculations(output):
    from backend.network.engine import calculate_network
    from backend.network.graph import load_network
    from backend.network.chukotavia import calculate_chukotavia
    from backend.network.russia import load_russia
    from backend.transport_calculator import compare_transport
    from backend.calculation_models import CalculationResponse

    requests = {
        'russia-request': ('russia-response', 'russia'),
        'chukotavia-request': ('chukotavia-response', 'air'),
        'river-request': ('river-response', 'river'),
        'network-request': ('network-response', 'network'),
        'network-four-modes': ('network-four-modes-response', 'network'),
        'network-free-cargo-request': ('network-free-cargo-response', 'network'),
        'network-unknown-cargo-request': ('network-unknown-cargo-response', 'network'),
        'network-declared-cargo-request': ('network-declared-cargo-response', 'network'),
        'calculation-request': ('calculation-response', 'legacy'),
        'calculation-stops': ('calculation-stops-response', 'legacy'),
    }
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for request_name, (response_name, engine) in requests.items():
        request = json.loads((ROOT / 'examples' / (request_name + '.json')).read_text(encoding='utf-8'))
        if engine == 'russia':
            result = load_russia().calculate(request)
        elif engine == 'air':
            result = calculate_chukotavia(request)
        elif engine == 'legacy':
            result = CalculationResponse.model_validate(compare_transport(request)).model_dump(mode="json")
        else:
            network = load_network(ROOT / 'data_sources/river/network.json') if engine == 'river' else load_network()
            result = calculate_network(request, network)
        path = output / (response_name + '.json')
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        written.append(path)
    return written


def main():
    parser = argparse.ArgumentParser(description='Восстановить производные примеры и CSV из исходных снимков')
    parser.add_argument('--kind', choices=('all', 'examples', 'asop'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/generated')
    args = parser.parse_args()
    paths = []
    if args.kind in ('all', 'examples'):
        paths.extend(export_calculations(args.output / 'examples'))
    if args.kind in ('all', 'asop'):
        paths.extend(export_asop(args.output / 'asop'))
    print(f'Сохранено файлов: {len(paths)}. Каталог: {args.output}')


if __name__ == '__main__':
    main()
