"""Build public aggregate statistics from the local, untracked corpus."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean, quantiles, median

ROOT = Path(__file__).resolve().parents[1]
MIN_BANK = 20
MIN_GROUP = 10
POS = {'S', 'V', 'A', 'ADV', 'PR', 'CONJ', 'SPRO', 'APRO', 'ADVPRO',
       'PART', 'ANUM', 'NUM', 'INTJ', 'COM', 'PUNCT'}
TOPICS = {
    'карта': ('карта',), 'кредит': ('кредит',), 'вклад': ('вклад',),
    'комиссия': ('комиссия',), 'кешбэк / кэшбэк': ('кешбэк', 'кэшбэк'),
    'приложение': ('приложение',), 'перевод': ('перевод',),
    'платёж / платеж': ('платёж', 'платеж'), 'поддержка': ('поддержка',),
    'отделение': ('отделение',), 'банкомат': ('банкомат',),
    'списание': ('списание',), 'обслуживание': ('обслуживание',),
    'подписка': ('подписка',), 'доставка': ('доставка',),
    'счёт / счет': ('счёт', 'счет'), 'процент': ('процент',),
    'оператор': ('оператор',), 'лимит': ('лимит',), 'тариф': ('тариф',),
    'возврат': ('возврат',), 'условие': ('условие',),
}


def pct(n, total):
    return round(100 * n / total, 2) if total else None


def distribution(values):
    if len(values) < MIN_GROUP:
        return None
    cuts = quantiles(values, n=100, method='inclusive')
    return dict(mean=round(mean(values), 2), median=median(values),
                p25=round(cuts[24], 2), p75=round(cuts[74], 2),
                p90=round(cuts[89], 2), p95=round(cuts[94], 2))


def groups(items, size, other, threshold):
    """Coalesce small groups; avoid a residual total below the threshold."""
    ordered = sorted(items, key=lambda row: (size(row[1]), row[0]))
    small = [v for k, v in ordered if size(v) < threshold]
    kept = [(k, v) for k, v in ordered if size(v) >= threshold]
    if small and 0 < sum(map(size, small)) < MIN_GROUP and kept:
        small.append(kept.pop(0)[1])
    return kept + ([(other, [v for group in small for v in group])] if small else [])


def calculate(csv_file, annotation):
    source, inputs = {}, 0
    with csv_file.open(encoding='utf-8-sig', newline='') as file:
        for row in csv.DictReader(file):
            inputs += 1
            key = row['link']
            if key in source and source[key] != row:
                raise ValueError('Conflicting duplicate in input CSV')
            source[key] = row
    if len(source) < MIN_BANK:
        raise ValueError('Too few reviews for a public report')
    by_bank, by_month, by_rating = defaultdict(list), defaultdict(list), defaultdict(list)
    sentiments = Counter()
    grades, comments = [], []
    lengths = defaultdict(list)
    seen, lemmas = set(), set()
    pos_counts, topics_tokens, topics_reviews = Counter(), Counter(), Counter()
    all_tokens = sentences = lemmatized = punctuation = no_lemma_nonpunct = ambiguous = 0
    with annotation.open(encoding='utf-8') as file:
        for line in file:
            row = json.loads(line)
            key = row['source_path']
            if key in seen or key not in source or source[key]['text'] != row['text']:
                raise ValueError('Annotation and original CSV do not match')
            seen.add(key)
            original = source[key]
            grade = int(original['grade'])
            if not 1 <= grade <= 5 or original['sentiment'] not in {'positive', 'negative'}:
                raise ValueError('Unexpected rating or sentiment')
            grades.append(grade)
            date = datetime.fromisoformat(original['datetime'].replace('Z', '+00:00')).astimezone(timezone.utc)
            by_month[date.strftime('%Y-%m')].append(grade)
            by_bank[original['bank']].append(grade)
            by_rating[str(grade)].append(grade)
            sentiments[original['sentiment']] += 1
            count = original['comments_count'].strip()
            if count not in ('', 'NA'):
                comments.append(int(count))
            lengths['characters_per_review'].append(len(row['text']))
            lengths['sentences_per_review'].append(len(row['sentences']))
            sentences += len(row['sentences'])
            review_token_count = 0
            found = Counter()
            for sentence in row['sentences']:
                review_token_count += len(sentence['tokens'])
                lengths['tokens_per_sentence'].append(len(sentence['tokens']))
                for token in sentence['tokens']:
                    tag = token['part_of_speech']
                    pos_counts[tag if tag in POS else None] += 1
                    punctuation += tag == 'PUNCT'
                    ambiguous += bool(token['mystem_alternatives'])
                    if token['lemma']:
                        lemma = token['lemma']
                        lemmas.add(lemma)
                        found[lemma] += 1
                        lemmatized += 1
                    else:
                        no_lemma_nonpunct += tag != 'PUNCT'
            for topic, forms in TOPICS.items():
                hits = sum(found[form] for form in forms)
                if hits:
                    topics_reviews[topic] += 1
                    topics_tokens[topic] += hits
            lengths['tokens_per_review'].append(review_token_count)
            all_tokens += review_token_count
    if seen != set(source) or sum(pos_counts.values()) != all_tokens:
        raise ValueError('Annotation is incomplete')

    n = len(grades)
    def summary(label, values):
        return dict(group=label, reviews=len(values), share_pct=pct(len(values), n),
                    mean_grade=round(mean(values), 2))
    bank_rows = [summary(label, values) for label, values in groups(
        by_bank.items(), len, 'Прочие банки', MIN_BANK)]
    bank_rows.sort(key=lambda row: (row['group'] == 'Прочие банки', -row['reviews'], row['group']))
    month_rows = [summary(label, values) for label, values in groups(
        by_month.items(), len, 'Прочие месяцы', MIN_GROUP)]
    month_rows.sort(key=lambda row: row['group'])
    # Keep fixed adjacent rating bands; the individual grade-3 cell is sparse.
    rating_counts = [('1', len(by_rating['1'])), ('2–3', len(by_rating['2']) + len(by_rating['3'])),
                     ('4', len(by_rating['4'])), ('5', len(by_rating['5']))]
    def counts(entries, total):
        visible = [(label, value) for label, value in entries if value >= MIN_GROUP]
        small = total - sum(value for _, value in visible)
        if 0 < small < MIN_GROUP and visible:
            least = min(visible, key=lambda pair: pair[1])
            visible.remove(least)
            small += least[1]
        if small:
            visible.append(('Прочее / не указано', small))
        return [dict(group=label, count=value, share_pct=pct(value, total)) for label, value in visible]
    pos_order = sorted(((tag or 'Без тега', count) for tag, count in pos_counts.items()),
                       key=lambda item: (-item[1], item[0]))
    return dict(
        input=dict(csv_rows=inputs, duplicate_rows=inputs - n, reviews=n, banks=len(by_bank)),
        rating=dict(mean=round(mean(grades), 2), median=median(grades),
                    groups=counts(rating_counts, n)),
        sentiment=counts([('Положительная', sentiments['positive']), ('Отрицательная', sentiments['negative'])], n),
        by_bank=bank_rows, by_month_utc=month_rows,
        comments=dict(known=len(comments), missing=n-len(comments), known_pct=pct(len(comments), n),
                      average_known=round(mean(comments), 2) if len(comments) >= MIN_GROUP else None,
                      median_known=median(comments) if len(comments) >= MIN_GROUP else None),
        annotation=dict(sentences=sentences, tokens=all_tokens, unique_lemmas=len(lemmas),
                        tokens_with_lemma=lemmatized, tokens_without_lemma=all_tokens-lemmatized,
                        nonpunctuation_tokens_without_lemma=no_lemma_nonpunct, punctuation=punctuation,
                        tokens_with_multiple_analyses=ambiguous if ambiguous >= MIN_GROUP else None,
                        lemma_coverage_pct=pct(lemmatized, all_tokens),
                        nonpunctuation_lemma_coverage_pct=pct(all_tokens-punctuation-no_lemma_nonpunct, all_tokens-punctuation)),
        length_distributions={k: distribution(v) for k, v in lengths.items()},
        part_of_speech=counts(pos_order, all_tokens),
        selected_lemmas=[dict(lemma=label, reviews=hits, reviews_share_pct=pct(hits, n), tokens=topics_tokens[label])
                         for label, hits in sorted(topics_reviews.items(), key=lambda item: (-item[1], item[0]))
                         if hits >= MIN_GROUP],
    )


def markdown(s):
    def format_value(x):
        if x is None:
            return '—'
        if isinstance(x, int):
            return f'{x:,}'.replace(',', ' ')
        if isinstance(x, float):
            return f'{x:.2f}'.replace('.', ',')
        return str(x).replace('|', r'\|').replace('\n', ' ')

    def table(headers, rows):
        return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                          '| ' + ' | '.join(['---'] + ['---:'] * (len(headers)-1)) + ' |'] +
                         ['| ' + ' | '.join(map(format_value, row)) + ' |' for row in rows])

    def rows(data, *keys):
        return ([row[key] for key in keys] for row in data)

    inp, a, c, r = s['input'], s['annotation'], s['comments'], s['rating']
    base = table(['Показатель', 'Значение'], [
        ('Строк в исходном CSV', inp['csv_rows']), ('Полных дубликатов', inp['duplicate_rows']),
        ('Уникальных отзывов', inp['reviews']), ('Банков', inp['banks']),
        ('Средняя оценка', r['mean']), ('Медианная оценка', r['median']),
        ('Предложений', a['sentences']), ('Токенов', a['tokens']), ('Уникальных лемм', a['unique_lemmas'])])
    counts_header = ['Группа', 'Количество', 'Доля, %']
    reviews_header = ['Группа', 'Отзывов', 'Доля отзывов, %', 'Средняя оценка']
    lengths = {'characters_per_review':'Символов в отзыве', 'sentences_per_review':'Предложений в отзыве',
               'tokens_per_review':'Токенов в отзыве', 'tokens_per_sentence':'Токенов в предложении'}
    sections = [
        '# Статистика корпуса банковских отзывов',
        'Сводка по локальной учебной выборке отзывов [Banki.ru](https://www.banki.ru/). '
        'Репрезентативность выборки не проверялась: доли и оценки характеризуют только данный корпус, '
        'а не качество банков и не весь сайт. В отчёте нет текстов, ссылок и строк отдельных отзывов.',
        '## Объём и оценки', base,
        table(['Оценка', 'Отзывов', 'Доля отзывов, %'], rows(r['groups'], 'group', 'count', 'share_pct')),
        'Оценки 2 и 3 объединены. Для средней и медианной оценки использованы исходные значения.',
        '## Тональность из исходного CSV',
        table(counts_header, rows(s['sentiment'], 'group', 'count', 'share_pct')),
        'Тональность — метка из CSV. Её нельзя считать независимой оценкой качества модели.',
        '## Банки',
        table(reviews_header, rows(s['by_bank'], 'group', 'reviews', 'share_pct', 'mean_grade')),
        'Названия показаны для банков с 20 и более отзывами; остальные объединены. Средние рассчитаны по отзывам, а не по банкам.',
        '## Месяцы публикации (UTC)',
        table(reviews_header, rows(s['by_month_utc'], 'group', 'reviews', 'share_pct', 'mean_grade')),
        'Крайние месяцы покрыты не целиком: сравнение объёмов не показывает помесячную динамику сайта.',
        '## Комментарии',
        table(['Показатель', 'Значение'], [('Число комментариев известно', c['known']),
            ('Число комментариев неизвестно', c['missing']), ('Доля известных, %', c['known_pct']),
            ('Среднее среди известных', c['average_known']), ('Медиана среди известных', c['median_known'])]),
        'Пропуски не заменялись нулями. Известный ноль учитывается в расчётах.',
        '## Длина текстов',
        table(['Единица измерения', 'Среднее', 'P25', 'Медиана', 'P75', 'P90', 'P95'],
              ([lengths[k], *(v[key] for key in ('mean', 'p25', 'median', 'p75', 'p90', 'p95'))]
               for k, v in s['length_distributions'].items() if v)),
        'Например, P90 — 90-й процентиль. Символы включают пробелы и переводы строк; токены включают пунктуацию.',
        '## Лингвистическая разметка',
        table(['Показатель', 'Значение'], [('Токенов с леммой', a['tokens_with_lemma']),
            ('Токенов без леммы, включая пунктуацию', a['tokens_without_lemma']),
            ('Токенов без леммы и без пунктуации', a['nonpunctuation_tokens_without_lemma']),
            ('Знаков пунктуации', a['punctuation']), ('Токенов с несколькими вариантами разбора', a['tokens_with_multiple_analyses']),
            ('Доля токенов с леммой, %', a['lemma_coverage_pct']),
            ('Доля токенов с леммой без учёта пунктуации, %', a['nonpunctuation_lemma_coverage_pct'])]),
        table(['Тег MyStem или служебная метка', 'Токенов', 'Доля токенов, %'], rows(s['part_of_speech'], 'group', 'count', 'share_pct')),
        '`S` — существительное, `V` — глагол, `A` — прилагательное, `PR` — предлог. Редкие категории объединены.',
        '## Выбранные банковские леммы',
        table(['Лемма', 'Отзывов с леммой', 'Доля отзывов, %', 'Вхождений'],
              rows(s['selected_lemmas'], 'lemma', 'reviews', 'reviews_share_pct', 'tokens')),
        'Слова выбраны заранее как примеры банковской лексики. Это не общий топ лемм и не оценка тематики: '
        'один отзыв может попасть в несколько строк, а совпадение слова не обязательно описывает основной предмет отзыва. '
        'Показаны термины, встретившиеся как минимум в 10 разных отзывах.',
        '## Воспроизводимость',
        'Локальные источники — исходный CSV и разметка MyStem + Razdel. Генератор удаляет полные дубли по ссылке '
        'и сверяет каждый текст разметки с исходником. Опубликованы только агрегаты. '
        'Публичная сводка не включает редкие фразы, даты отдельных отзывов и разбивки «банк × дата × оценка».',
        '```powershell\npython scripts/generate_corpus_statistics.py\n```',
        'Те же агрегаты доступны в [JSON](corpus-statistics.json). Для пересчёта нужны локальные файлы из README.',
    ]
    return '\n\n'.join(sections) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-root', type=Path, default=ROOT)
    p.add_argument('--output-dir', type=Path, default=ROOT / 'docs')
    args = p.parse_args()
    stats = calculate(args.input_root / 'data/raw/bankiru_fulltext.csv',
                      args.input_root / 'data/processed/mystem_annotation_review.jsonl')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'corpus-statistics.md').write_text(markdown(stats), encoding='utf-8', newline='\n')
    (args.output_dir / 'corpus-statistics.json').write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
