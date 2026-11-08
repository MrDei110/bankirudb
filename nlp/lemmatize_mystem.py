"""Annotate CSV with Razdel and MyStem; database is not modified."""
import argparse
import csv
import json
import pathlib
import re
import random
import time
import difflib
from types import SimpleNamespace
from importlib.metadata import version

ROOT = pathlib.Path(__file__).resolve().parents[1]

def analyze_sentence(text, analyzer, tokenize):
    # pymystem3 processes lines separately. Replace whitespace character-for-
    # character for analysis only; original text and coordinates stay intact.
    normalized = ''.join(' ' if c.isspace() else c for c in text)
    spans = []
    cursor = 0
    raw = analyzer.analyze(normalized)
    reconstructed = ''.join(item.get('text', '') for item in raw)
    returned_text = reconstructed[:-1] if reconstructed.endswith('\n') else reconstructed
    coordinate_map = {}
    recoverable = True
    for kind, a, b, c, d in difflib.SequenceMatcher(None, normalized, returned_text, autojunk=False).get_opcodes():
        if kind == 'equal':
            coordinate_map.update((c + offset, a + offset) for offset in range(b - a))
        elif kind == 'delete' and not any(char.isalnum() for char in normalized[a:b]):
            # MyStem can omit punctuation such as '+'; restore it from source.
            pass
        else:
            recoverable = False
    # MyStem adds a terminal newline, sometimes grouped with punctuation.
    # Remove only the synthetic suffix, never characters from the source.
    if not recoverable:
        diagnostic = ROOT / 'data' / 'experiments' / 'mystem_alignment_error.json'
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        diagnostic.write_text(json.dumps(dict(text=text, normalized=normalized,
            reconstructed=reconstructed, mystem_output=raw), ensure_ascii=False, indent=2), encoding='utf-8')
        raise ValueError(f'MyStem changed source text; diagnostic saved to {diagnostic}')
    for item in raw:
        surface = item.get('text', '')
        output_start = cursor
        cursor += len(surface)
        output_end = min(cursor, len(returned_text))
        surface = surface[:max(0, output_end - output_start)]
        if not surface or surface.isspace():
            continue
        start = coordinate_map[output_start]
        end = coordinate_map[output_end - 1] + 1
        analyses = item.get('analysis') or []
        if len(analyses) > 1:
            # Do not invent a winner. Preserve all alternatives and use only
            # lemma/POS values shared by every returned analysis.
            lemmas = {a.get('lex') for a in analyses}
            parts = {re.split(r'[,=]', a.get('gr', ''), maxsplit=1)[0] or None
                     for a in analyses}
            analysis = dict(lex=next(iter(lemmas)) if len(lemmas) == 1 else None,
                gr='', _pos=next(iter(parts)) if len(parts) == 1 else None,
                _alternatives=analyses)
        else:
            analysis = analyses[0] if analyses else None
        if analysis and end - start != len(surface):
            raise ValueError('MyStem omitted punctuation inside an analysed word')
        spans.append((start, end, analysis))
    # Use MyStem word boundaries for morphology. Razdel only splits fragments
    # without analysis (numbers/punctuation). This avoids losing lemmas when
    # Razdel treats a hyphenated expression as one token and MyStem splits it.
    segmented = []
    filled_spans = []
    previous = 0
    for start, end, analysis in spans:
        if start > previous:
            filled_spans.append((previous, start, None))
        filled_spans.append((start, end, analysis))
        previous = end
    if previous < len(text):
        filled_spans.append((previous, len(text), None))
    spans = filled_spans
    for start, end, analysis in spans:
        if analysis:
            segmented.append(SimpleNamespace(start=start, stop=end, text=text[start:end]))
        else:
            for fragment in tokenize(text[start:end]):
                segmented.append(SimpleNamespace(start=start + fragment.start,
                    stop=start + fragment.stop, text=fragment.text))
    result = []
    previous_end = 0
    for position, token in enumerate(segmented, 1):
        if token.start < previous_end or text[previous_end:token.start].strip():
            raise ValueError('Token overlap or omitted non-whitespace text')
        previous_end = token.stop
        overlapping = [(a,b,m) for a,b,m in spans if a < token.stop and b > token.start and m]
        exact = [m for a,b,m in overlapping if a == token.start and b == token.stop]
        analysis = exact[0] if len(exact) == 1 else None
        lemma = analysis.get('lex') if analysis else None
        grammar = analysis.get('gr', '') if analysis else ''
        pos = re.split(r'[,=]', grammar, maxsplit=1)[0] or None
        if analysis and '_pos' in analysis:
            pos = analysis['_pos']
        if not any(c.isalnum() for c in token.text):
            lemma, pos = None, 'PUNCT'
        elif token.text.isdecimal() and pos is None:
            pos = 'NUM'
        if lemma and len(lemma) > 255:
            raise ValueError('Lemma exceeds database limit')
        if text[token.start:token.stop] != token.text:
            raise ValueError('Token coordinate mismatch')
        result.append(dict(position=position, surface=token.text, lemma=lemma,
            part_of_speech=pos, start_offset=token.start, end_offset=token.stop,
            mystem_grammar=grammar or None,
            mystem_alternatives=analysis.get('_alternatives', []) if analysis else [],
            alignment_issue=bool(overlapping and not analysis)))
    if text[previous_end:].strip():
        raise ValueError('Tokenizer omitted end of sentence')
    return result

def main():
    started_at = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int)
    parser.add_argument('--sample', type=int, help='Random sample with fixed seed 42')
    parser.add_argument('--mode', choices=['review', 'sentence'], default='review',
        help='One MyStem call per review (default) or the previous per-sentence mode')
    parser.add_argument('--resume', action='store_true', help='Continue validated temporary JSONL checkpoint')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    if args.sample is not None and (args.sample < 1 or args.limit is not None):
        parser.error('--sample must be positive and cannot be combined with --limit')
    from pymystem3 import Mystem
    from razdel import sentenize, tokenize
    source = ROOT / 'data' / 'raw' / 'bankiru_fulltext.csv'
    is_sample = args.limit is not None or args.sample is not None
    folder = ROOT / 'data' / ('experiments' if is_sample or args.mode == 'sentence' else 'processed')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('mystem_sample.jsonl' if is_sample else 'mystem_annotation.jsonl')
    preview_path = folder / ('mystem_sample_preview.csv' if is_sample else 'mystem_preview.csv')
    if args.mode == 'review':
        target = target.with_name(target.stem + '_review.jsonl')
        preview_path = preview_path.with_name(preview_path.stem + '_review.csv')
    temporary = target.with_suffix('.jsonl.tmp')
    unique = {}
    with source.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row['link'] in unique and unique[row['link']] != row:
                raise ValueError('Conflicting duplicate: ' + row['link'])
            unique[row['link']] = row
    rows = list(unique.values())
    if args.limit:
        rows = rows[:args.limit]
    elif args.sample:
        rows = random.Random(42).sample(rows, min(args.sample, len(rows)))
    print(f'Selected reviews: {len(rows)}; banks: {len({r["bank"] for r in rows})}; '
        f'positive: {sum(r["sentiment"] == "positive" for r in rows)}; '
        f'negative: {sum(r["sentiment"] == "negative" for r in rows)}', flush=True)
    sentence_count = token_count = unknown_count = alignment_count = ambiguous_count = 0
    analysis_calls = 0
    saved = []
    if args.resume:
        if not temporary.exists():
            parser.error('No temporary checkpoint exists for this mode')
        with temporary.open(encoding='utf-8') as checkpoint:
            for index, line in enumerate(checkpoint):
                record = json.loads(line)
                if index >= len(rows) or record['source_path'] != rows[index]['link'] or record['text'] != rows[index]['text'] or record.get('analysis_mode') != args.mode:
                    raise ValueError('Checkpoint does not match selected input/mode')
                saved.append(record)
        print(f'Resuming after {len(saved)} completed reviews; remaining: {len(rows)-len(saved)}', flush=True)
    analyzer = Mystem(disambiguation=True, grammar_info=True, entire_input=True)
    try:
        with temporary.open('a' if args.resume else 'w', encoding='utf-8', newline='\n') as out, preview_path.open('w', encoding='utf-8-sig', newline='') as preview:
            writer = csv.writer(preview, delimiter=';')
            writer.writerow(['source_path','sentence_position','token_position','surface','lemma','part_of_speech','start_offset','end_offset','alignment_issue'])
            for record in saved:
                for sentence in record['sentences']:
                    sentence_count += 1
                    for token in sentence['tokens']:
                        token_count += 1
                        unknown_count += token['lemma'] is None and token['part_of_speech'] != 'PUNCT'
                        alignment_count += token['alignment_issue']
                        ambiguous_count += bool(token['mystem_alternatives'])
                        writer.writerow([record['source_path'], sentence['position'], token['position'], token['surface'], token['lemma'], token['part_of_speech'], token['start_offset'], token['end_offset'], token['alignment_issue']])
            for index, row in enumerate(rows, 1):
                if index <= len(saved):
                    continue
                text = row['text']
                sentences = []
                previous_end = 0
                review_tokens = None
                token_cursor = 0
                if args.mode == 'review':
                    review_tokens = analyze_sentence(text, analyzer, tokenize)
                    analysis_calls += 1
                for position, sentence in enumerate(sentenize(text), 1):
                    if not (previous_end <= sentence.start < sentence.stop <= len(text)):
                        raise ValueError('Invalid sentence offsets')
                    if text[previous_end:sentence.start].strip():
                        raise ValueError('Sentence splitter omitted non-whitespace text')
                    sentence_text = text[sentence.start:sentence.stop]
                    if sentence_text != sentence.text:
                        raise ValueError('Sentence coordinate mismatch')
                    if review_tokens is None:
                        tokens = analyze_sentence(sentence_text, analyzer, tokenize)
                        analysis_calls += 1
                    else:
                        tokens = []
                        while token_cursor < len(review_tokens):
                            token = review_tokens[token_cursor]
                            if token['start_offset'] >= sentence.stop:
                                break
                            if token['start_offset'] < sentence.start or token['end_offset'] > sentence.stop:
                                raise ValueError(f'Token crosses sentence boundary in {row["link"]}: {token!r}')
                            local_token = dict(token, position=len(tokens) + 1,
                                start_offset=token['start_offset'] - sentence.start,
                                end_offset=token['end_offset'] - sentence.start)
                            if sentence_text[local_token['start_offset']:local_token['end_offset']] != local_token['surface']:
                                raise ValueError('Review-to-sentence token coordinate mismatch')
                            tokens.append(local_token)
                            token_cursor += 1
                    sentences.append(dict(position=position, text=sentence_text,
                        start_offset=sentence.start, end_offset=sentence.stop, tokens=tokens))
                    previous_end = sentence.stop
                    sentence_count += 1
                    token_count += len(tokens)
                    for token in tokens:
                        unknown_count += token['lemma'] is None and token['part_of_speech'] != 'PUNCT'
                        alignment_count += token['alignment_issue']
                        ambiguous_count += bool(token['mystem_alternatives'])
                        writer.writerow([row['link'], position, token['position'], token['surface'],
                            token['lemma'], token['part_of_speech'], token['start_offset'],
                            token['end_offset'], token['alignment_issue']])
                if not sentences or text[previous_end:].strip():
                    raise ValueError('Sentence extraction incomplete')
                if review_tokens is not None and token_cursor != len(review_tokens):
                    raise ValueError('Some review tokens have no sentence')
                out.write(json.dumps(dict(source_path=row['link'], text=text, sentences=sentences,
                    tokenization='mystem-words+razdel-unanalysed-v2',
                    analysis_mode=args.mode,
                    processor='mystem+razdel', pymystem3_version=version('pymystem3'),
                    razdel_version=version('razdel')), ensure_ascii=False) + '\n')
                if index % 100 == 0 or index == len(rows):
                    print(f'Processed {index}/{len(rows)} reviews', flush=True)
        temporary.replace(target)
    finally:
        analyzer.close()
    print(f'Reviews: {len(rows)}; sentences: {sentence_count}; tokens: {token_count}')
    print(f'Non-punctuation tokens without lemma (including numbers): {unknown_count}')
    print(f'Tokenizer alignment issues: {alignment_count}')
    print(f'Tokens with multiple MyStem analyses: {ambiguous_count}')
    print(f'MyStem calls: {analysis_calls}; mode: {args.mode}')
    print(f'Annotation: {target}')
    print(f'Preview: {preview_path}')
    print('Database unchanged.')
    elapsed = time.perf_counter() - started_at
    minutes, seconds = divmod(elapsed, 60)
    print(f'Total time: {int(minutes)} min {seconds:.1f} sec ({elapsed:.1f} sec)')
    if len(rows) > len(saved):
        print(f'Average time per newly processed review: {elapsed / (len(rows)-len(saved)):.2f} sec')

if __name__ == '__main__':
    main()
