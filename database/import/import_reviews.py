"""Import original Banki.ru CSV using Python and Docker; no pip packages needed."""
import csv
import datetime
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'data' / 'raw' / 'bankiru_fulltext.csv'
COMPOSE = ROOT / 'compose.yaml'

def quote(value):
    if value is None:
        return 'NULL'
    if '\x00' in value:
        raise ValueError('NUL character in source data')
    return "'" + value.replace("'", "''") + "'"

def main():
    with SOURCE.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'title', 'link', 'bank', 'grade', 'text', 'datetime', 'comments_count', 'sentiment'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Required CSV columns missing')
        rows = list(reader)
    unique = {}
    for row in rows:
        link = row['link']
        if link in unique:
            if row != unique[link]:
                raise ValueError('Conflicting duplicate link: ' + link)
            continue
        for field in ['title', 'link', 'bank', 'text']:
            if not row[field].strip():
                raise ValueError('Empty required field: ' + field)
        if int(row['grade']) not in range(1, 6):
            raise ValueError('Invalid grade')
        date = datetime.datetime.fromisoformat(row['datetime'].replace('Z', '+00:00'))
        if date.utcoffset() is None:
            raise ValueError('Timestamp has no timezone')
        count = row['comments_count'].strip()
        if count not in ('', 'NA') and int(count) < 0:
            raise ValueError('Negative comments count')
        if row['sentiment'] not in ('positive', 'negative'):
            raise ValueError('Invalid sentiment')
        unique[link] = row
    statements = [
        "BEGIN; SET LOCAL standard_conforming_strings = on;",
        "CREATE TEMP TABLE incoming (bank TEXT, source_path TEXT, title TEXT, text TEXT, grade SMALLINT, published_at TIMESTAMPTZ, comments_count INTEGER, sentiment TEXT) ON COMMIT DROP;",
    ]
    values = []
    for row in unique.values():
        count = row['comments_count'].strip()
        fields = [row['bank'], row['link'], row['title'], row['text'], row['grade'], row['datetime'], None if count in ('', 'NA') else count, row['sentiment']]
        values.append('(' + ','.join(quote(v) for v in fields) + ')')
    if values:
        statements.append('INSERT INTO incoming VALUES ' + ',\n'.join(values) + ';')
    statements.extend([
        "INSERT INTO banks (name) SELECT DISTINCT bank FROM incoming ON CONFLICT (name) DO NOTHING;",
        """DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM incoming i JOIN banks b ON b.name=i.bank
            JOIN reviews r ON r.source_path=i.source_path
            WHERE ROW(r.bank_id,r.title,r.text,r.grade,r.published_at,r.comments_count,r.sentiment)
            IS DISTINCT FROM ROW(b.bank_id,i.title,i.text,i.grade,i.published_at,i.comments_count,i.sentiment)
        ) THEN RAISE EXCEPTION 'Existing review differs from CSV; import rolled back'; END IF;
        END $$;""",
        """INSERT INTO reviews (bank_id,source_path,title,text,grade,published_at,comments_count,sentiment)
        SELECT b.bank_id,i.source_path,i.title,i.text,i.grade,i.published_at,i.comments_count,i.sentiment
        FROM incoming i JOIN banks b ON b.name=i.bank
        ON CONFLICT (source_path) DO NOTHING;""",
        """DO $$ BEGIN
        IF (SELECT count(*) FROM incoming) <> (SELECT count(*) FROM reviews r JOIN incoming i ON i.source_path=r.source_path)
        THEN RAISE EXCEPTION 'Review count verification failed'; END IF;
        END $$;""",
        "SELECT count(*) AS imported_csv_reviews FROM reviews r JOIN incoming i ON i.source_path=r.source_path;",
        "SELECT count(DISTINCT b.bank_id) AS csv_banks FROM banks b JOIN incoming i ON i.bank=b.name;",
        "COMMIT;",
    ])
    print(f'CSV rows: {len(rows)}; unique reviews: {len(unique)}; duplicate rows: {len(rows)-len(unique)}', flush=True)
    if '--check-only' in sys.argv:
        print('CSV validation passed; database not changed.')
        return
    command = ['docker', 'compose', '--env-file', str(ROOT / '.env'), '-f', str(COMPOSE),
               'exec', '-T', 'postgres', 'sh', '-c',
               'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    result = subprocess.run(command, input=('\n'.join(statements)).encode('utf-8'))
    if result.returncode:
        raise SystemExit(result.returncode)
    print('Import completed. Refresh tables in DBeaver.')

if __name__ == '__main__':
    main()
