"""Backfill deterministic direct PDF URLs for existing DBLP papers."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.dblp import infer_dblp_pdf_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--database',
        type=Path,
        default=PROJECT_ROOT / 'data' / 'papers.db',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Write inferred URLs. Without this flag the command is a dry run.',
    )
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    try:
        rows = connection.execute(
            """
            SELECT id, url
            FROM papers
            WHERE source = 'dblp'
              AND (pdf_url IS NULL OR trim(pdf_url) = '')
            """
        ).fetchall()
        updates = []
        for paper_id, landing_url in rows:
            pdf_url = infer_dblp_pdf_url([landing_url])
            if pdf_url:
                updates.append((pdf_url, paper_id))

        hosts = Counter(url.split('/')[2] for url, _paper_id in updates)
        print(f'Eligible DBLP papers: {len(updates)}')
        for host, count in hosts.most_common():
            print(f'  {host}: {count}')

        if args.apply and updates:
            columns = {
                row[1] for row in connection.execute('PRAGMA table_info(papers)')
            }
            statement = (
                "UPDATE papers SET pdf_url = ?, pdf_source = 'dblp_rule' WHERE id = ?"
                if 'pdf_source' in columns
                else 'UPDATE papers SET pdf_url = ? WHERE id = ?'
            )
            connection.executemany(
                statement, updates
            )
            connection.commit()
            print(f'Updated DBLP papers: {len(updates)}')
        else:
            print('Dry run only; database was not changed.')
    finally:
        connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
