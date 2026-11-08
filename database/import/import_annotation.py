"""Validate and import annotation using Docker psql; no extra Python packages."""
import argparse
import csv
import io
import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]

def validate(records):
    paths = set()
    lemmas = set()
    sentence_count = token_count = 0
    def check(condition, message):
        if not condition:
            raise ValueError(message)
    for record in records:
        path, text = record['source_path'], record['text']
        check(path not in paths, 'Duplicate source_path: ' + path)
        paths.add(path)
        check(record.get('analysis_mode') == 'review', 'Unexpected analysis mode')
        check(bool(record['sentences']), 'Review has no sentences')
        end = 0
        for i, sentence in enumerate(record['sentences'], 1):
            start, stop = sentence['start_offset'], sentence['end_offset']
            check(sentence['position'] == i and end <= start < stop <= len(text), 'Invalid sentence position/offset')
            check(not text[end:start].strip(), 'Lost text between sentences')
            check(text[start:stop] == sentence['text'], 'Sentence text mismatch')
            end = stop
            sentence_count += 1
            token_end = 0
            check(bool(sentence['tokens']), 'Sentence has no tokens')
            for j, token in enumerate(sentence['tokens'], 1):
                a, b = token['start_offset'], token['end_offset']
                check(token['position'] == j and token_end <= a < b <= len(sentence['text']), 'Invalid token position/offset')
                check(not sentence['text'][token_end:a].strip(), 'Lost text between tokens')
                check(sentence['text'][a:b] == token['surface'], 'Token surface mismatch')
                check(not token.get('alignment_issue'), 'Unresolved token alignment')
                token_end = b
                token_count += 1
                lemma = token['lemma']
                check(lemma is None or isinstance(lemma, str) and 0 < len(lemma) <= 255 and bool(lemma.strip()), 'Invalid lemma')
                pos = token['part_of_speech']
                check(pos is None or isinstance(pos, str) and 0 < len(pos) <= 32 and bool(pos.strip()), 'Invalid part of speech')
                if lemma:
                    lemmas.add(lemma)
            check(not sentence['text'][token_end:].strip(), 'Lost sentence ending')
        check(not text[end:].strip(), 'Lost review ending')
    return sentence_count, token_count, len(lemmas)

SQL = r'''
CREATE TEMP TABLE input_reviews ON COMMIT DROP AS
SELECT r.review_id, a.payload
FROM annotation_input a JOIN reviews r ON r.source_path = a.payload->>'source_path';
DO $$ BEGIN
IF (SELECT count(*) FROM input_reviews) <> (SELECT count(*) FROM annotation_input)
THEN RAISE EXCEPTION 'Some reviews not found in database'; END IF;
IF EXISTS (SELECT 1 FROM input_reviews i JOIN reviews r USING (review_id)
           WHERE r.text IS DISTINCT FROM i.payload->>'text')
THEN RAISE EXCEPTION 'Database review text differs from annotation'; END IF;
END $$;

CREATE TEMP TABLE input_sentences ON COMMIT DROP AS
SELECT i.review_id, (s->>'position')::integer AS position, s->>'text' AS text,
       (s->>'start_offset')::integer AS start_offset,
       (s->>'end_offset')::integer AS end_offset, s->'tokens' AS tokens
FROM input_reviews i CROSS JOIN LATERAL jsonb_array_elements(i.payload->'sentences') s;
ALTER TABLE input_sentences ADD PRIMARY KEY (review_id, position);

CREATE TEMP TABLE input_tokens ON COMMIT DROP AS
SELECT s.review_id, s.position AS sentence_position,
       (t->>'position')::integer AS position, t->>'surface' AS surface,
       t->>'lemma' AS lemma, t->>'part_of_speech' AS part_of_speech,
       (t->>'start_offset')::integer AS start_offset,
       (t->>'end_offset')::integer AS end_offset
FROM input_sentences s CROSS JOIN LATERAL jsonb_array_elements(s.tokens) t;
ALTER TABLE input_tokens ADD PRIMARY KEY (review_id, sentence_position, position);
ANALYZE input_sentences;
ANALYZE input_tokens;

-- Existing matching rows are kept. Conflicting rows cause a full rollback.
DO $$ BEGIN
IF EXISTS (
 SELECT 1 FROM sentences s JOIN input_reviews r USING (review_id)
 LEFT JOIN input_sentences i ON i.review_id=s.review_id AND i.position=s.position
 WHERE i.review_id IS NULL OR ROW(s.text,s.start_offset,s.end_offset)
 IS DISTINCT FROM ROW(i.text,i.start_offset,i.end_offset)
) THEN RAISE EXCEPTION 'Existing sentences conflict with annotation'; END IF;
IF EXISTS (
 SELECT 1 FROM tokens t JOIN sentences s USING(sentence_id)
 JOIN input_reviews r ON r.review_id=s.review_id
 LEFT JOIN lemmas l ON l.lemma_id=t.lemma_id
 LEFT JOIN input_tokens i ON i.review_id=s.review_id AND i.sentence_position=s.position AND i.position=t.position
 WHERE i.review_id IS NULL OR ROW(t.surface,l.form,t.part_of_speech,t.start_offset,t.end_offset)
 IS DISTINCT FROM ROW(i.surface,i.lemma,i.part_of_speech,i.start_offset,i.end_offset)
) THEN RAISE EXCEPTION 'Existing tokens conflict with annotation'; END IF;
END $$;

INSERT INTO lemmas(form)
SELECT DISTINCT lemma FROM input_tokens WHERE lemma IS NOT NULL
ON CONFLICT (form) DO NOTHING;
INSERT INTO sentences(review_id,position,text,start_offset,end_offset)
SELECT review_id,position,text,start_offset,end_offset FROM input_sentences
ON CONFLICT (review_id,position) DO NOTHING;
INSERT INTO tokens(sentence_id,lemma_id,position,surface,part_of_speech,start_offset,end_offset)
SELECT s.sentence_id,l.lemma_id,i.position,i.surface,i.part_of_speech,i.start_offset,i.end_offset
FROM input_tokens i JOIN sentences s ON s.review_id=i.review_id AND s.position=i.sentence_position
LEFT JOIN lemmas l ON l.form=i.lemma
ON CONFLICT (sentence_id,position) DO NOTHING;

DO $$ BEGIN
IF (SELECT count(*) FROM input_sentences) <>
   (SELECT count(*) FROM sentences s JOIN input_reviews r USING(review_id))
THEN RAISE EXCEPTION 'Sentence count verification failed'; END IF;
IF (SELECT count(*) FROM input_tokens) <>
   (SELECT count(*) FROM tokens t JOIN sentences s USING(sentence_id) JOIN input_reviews r USING(review_id))
THEN RAISE EXCEPTION 'Token count verification failed'; END IF;
END $$;
SELECT (SELECT count(*) FROM input_reviews) AS checked_reviews,
       (SELECT count(*) FROM input_sentences) AS sentences,
       (SELECT count(*) FROM input_tokens) AS tokens,
       (SELECT count(DISTINCT lemma) FROM input_tokens) AS annotation_lemmas;
COMMIT;
'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    started = time.perf_counter()
    source = ROOT / 'data' / 'processed' / 'mystem_annotation_review.jsonl'
    with source.open(encoding='utf-8') as f:
        records = [json.loads(line) for line in f]
    sentences, tokens, lemmas = validate(records)
    if not records:
        raise ValueError('Annotation file is empty')
    print(f'Validated reviews: {len(records)}; sentences: {sentences}; tokens: {tokens}; unique lemmas: {lemmas}', flush=True)
    if args.check_only:
        print('Validation passed; database unchanged.')
        return
    buffer = io.StringIO(newline='')
    buffer.write('BEGIN;\nSET LOCAL client_encoding = \'UTF8\';\nCREATE TEMP TABLE annotation_input (payload JSONB NOT NULL) ON COMMIT DROP;\nCOPY annotation_input(payload) FROM STDIN WITH (FORMAT csv);\n')
    writer = csv.writer(buffer, lineterminator='\n')
    for record in records:
        # Raw morphology alternatives remain in the annotation file, not the
        # five-table schema. Staging contains only data required by the import.
        compact = dict(source_path=record['source_path'], text=record['text'], sentences=[])
        for sentence in record['sentences']:
            item = {k:v for k,v in sentence.items() if k != 'tokens'}
            item['tokens'] = [{k:t[k] for k in ('position','surface','lemma','part_of_speech','start_offset','end_offset')} for t in sentence['tokens']]
            compact['sentences'].append(item)
        writer.writerow([json.dumps(compact, ensure_ascii=False)])
    buffer.write('\\.\n')
    buffer.write(SQL)
    command = ['docker', 'compose', '--env-file', str(ROOT / '.env'), '-f', str(ROOT / 'compose.yaml'),
               'exec', '-T', 'postgres', 'sh', '-c',
               'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    print('Importing in one transaction; please wait...', flush=True)
    result = subprocess.run(command, input=buffer.getvalue().encode('utf-8'))
    if result.returncode:
        raise SystemExit(result.returncode)
    print(f'Import completed in {time.perf_counter()-started:.1f} sec. Refresh DBeaver.')

if __name__ == '__main__':
    main()
