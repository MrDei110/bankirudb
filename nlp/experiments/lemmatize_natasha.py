"""Create structured annotation; does not modify the database."""
import argparse
import csv
import json
import pathlib
import importlib.metadata

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='Process only the first N unique reviews')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    from natasha import Segmenter, MorphVocab, NewsEmbedding, NewsMorphTagger, Doc
    segmenter = Segmenter()
    vocab = MorphVocab()
    tagger = NewsMorphTagger(NewsEmbedding())
    root = pathlib.Path(__file__).resolve().parents[2]
    source = root / 'data' / 'raw' / 'bankiru_fulltext.csv'
    target = root / 'data' / 'experiments' / ('natasha_sample.jsonl' if args.limit else 'natasha_annotation.jsonl')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.jsonl.tmp')
    with source.open(encoding='utf-8-sig', newline='') as f:
        unique = {}
        for row in csv.DictReader(f):
            previous = unique.get(row['link'])
            if previous is not None and previous != row:
                raise ValueError('Conflicting duplicate: ' + row['link'])
            unique[row['link']] = row
    rows = list(unique.values())
    if args.limit:
        rows = rows[:args.limit]
    sentence_count = token_count = 0
    with temporary.open('w', encoding='utf-8', newline='\n') as out:
        for index, row in enumerate(rows, 1):
            text = row['text']
            doc = Doc(text)
            doc.segment(segmenter)
            doc.tag_morph(tagger)
            for token in doc.tokens:
                if token.pos != 'PUNCT':
                    token.lemmatize(vocab)
            sentences = []
            last_sentence_end = 0
            for position, sent in enumerate(doc.sents, 1):
                if not (last_sentence_end <= sent.start < sent.stop <= len(text)):
                    raise ValueError('Invalid sentence coordinates')
                sentence_text = text[sent.start:sent.stop]
                if sentence_text != sent.text:
                    raise ValueError('Sentence does not match original text')
                tokens = []
                last_token_end = 0
                for token_position, token in enumerate(sent.tokens, 1):
                    start, end = token.start - sent.start, token.stop - sent.start
                    if not (last_token_end <= start < end <= len(sentence_text)):
                        raise ValueError('Invalid token coordinates')
                    if sentence_text[start:end] != token.text:
                        raise ValueError('Token does not match original text')
                    lemma = None if token.pos == 'PUNCT' else token.lemma
                    if lemma and len(lemma) > 255:
                        raise ValueError('Lemma exceeds database limit')
                    tokens.append(dict(position=token_position, surface=token.text,
                        lemma=lemma, part_of_speech=token.pos,
                        start_offset=start, end_offset=end))
                    last_token_end = end
                sentences.append(dict(position=position, text=sentence_text,
                    start_offset=sent.start, end_offset=sent.stop, tokens=tokens))
                last_sentence_end = sent.stop
                sentence_count += 1
                token_count += len(tokens)
            if sum(len(s['tokens']) for s in sentences) != len(doc.tokens):
                raise ValueError('Some tokens are not assigned to sentences')
            if not sentences:
                raise ValueError('No sentences extracted: ' + row['link'])
            record = dict(source_path=row['link'], text=text, sentences=sentences,
                processor='natasha', processor_version=importlib.metadata.version('natasha'))
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
            if index == 1:
                preview = tokens_preview = sentences[0]['tokens'][:15]
                print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
            if index % 100 == 0 or index == len(rows):
                print(f'Processed {index}/{len(rows)} reviews', flush=True)
    temporary.replace(target)
    print(f'Reviews: {len(rows)}; sentences: {sentence_count}; tokens: {token_count}')
    print(f'Output: {target}')
    print('Database unchanged.')

if __name__ == '__main__':
    main()
