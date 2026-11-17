import os
import re
from collections import defaultdict
from flask import Flask, abort, render_template, request
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.config['DEMO_DATA'] = os.environ.get('BANKIRU_DEMO') == '1'
PAGE_SIZE = 12
SEARCH_MODES = {'text': 'По тексту', 'lemma': 'По лемме', 'form': 'По точной словоформе'}
POS_LABELS = {
    'S': 'Существительное', 'A': 'Прилагательное', 'V': 'Глагол',
    'ADV': 'Наречие', 'SPRO': 'Местоимение', 'APRO': 'Местоимение-прилагательное',
    'ADVPRO': 'Местоименное наречие', 'NUM': 'Числительное',
    'ANUM': 'Порядковое числительное', 'PR': 'Предлог', 'CONJ': 'Союз',
    'PART': 'Частица', 'INTJ': 'Междометие', 'COM': 'Часть сложного слова',
}


def search_options():
    # Keep links from the previous version of the app working.
    legacy = request.args.get('lemma', '')
    q = request.args.get('q', legacy).replace('\x00', '').strip()[:100]
    mode = request.args.get('mode', 'lemma' if legacy else 'text')
    pos = request.args.get('pos', '')
    bank = request.args.get('bank', 0, type=int)
    grade = request.args.get('grade', 0, type=int)
    return dict(q=q, mode=mode if mode in SEARCH_MODES else 'text',
                pos=pos if pos in POS_LABELS else '',
                bank=bank if 0 < bank < 2**63 else 0,
                grade=grade if 1 <= grade <= 5 else 0)


def like_pattern(q):
    return '%' + q.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


def token_search(q, mode, pos, **_):
    """Both the result filter and the highlighter use these token conditions."""
    if not pos and (not q or mode == 'text'):
        return None
    source = 'public.sentences s JOIN public.tokens t USING (sentence_id)'
    conditions, params = [], []
    if q and mode == 'lemma':
        source += ' JOIN public.lemmas l USING (lemma_id)'
        conditions.append('l.form = lower(%s)')
        params.append(q)
    elif q and mode == 'form':
        conditions.append('lower(t.surface) = lower(%s)')
        params.append(q)
    elif q:
        conditions.append("t.surface ILIKE %s ESCAPE '!'")
        params.append(like_pattern(q))
    if pos:
        conditions.append('t.part_of_speech = %s')
        params.append(pos)
    return source, ' AND '.join(conditions), params


def token_matches(conn, search, review_ids):
    if search is None or not review_ids:
        return []
    source, condition, params = search
    return conn.execute(f'''
        SELECT s.review_id, s.position, s.text, s.start_offset AS sentence_start,
               t.start_offset, t.end_offset
        FROM {source}
        WHERE s.review_id = ANY(%s) AND {condition}
        ORDER BY s.review_id, s.position, t.position
    ''', [review_ids, *params]).fetchall()


def literal_spans(text, q):
    return [m.span() for m in re.finditer(re.escape(q), text, re.IGNORECASE)] if q else []


def text_parts(text, spans):
    """Return plain strings for Jinja autoescaping, plus highlight flags."""
    parts, cursor = [], 0
    for start, end in sorted(spans):
        if not cursor <= start < end <= len(text):
            continue
        if start > cursor:
            parts.append((text[cursor:start], False))
        parts.append((text[start:end], True))
        cursor = end
    if cursor < len(text):
        parts.append((text[cursor:], False))
    return parts


def excerpt(text, spans, width=230):
    first, first_end = spans[0] if spans else (0, 0)
    start = max(0, first - 50)
    end = min(len(text), max(start + width, first_end))
    local = [(max(a, start) - start, min(b, end) - start)
             for a, b in spans if a < end and b > start]
    return ([("…", False)] if start else []) + text_parts(text[start:end], local) + (
        [("…", False)] if end < len(text) else [])


def db():
    # libpq reads PGHOST, PGUSER, PGPASSWORD and PGDATABASE from Compose.
    return psycopg.connect(row_factory=dict_row, connect_timeout=4,
                           options='-c default_transaction_read_only=on -c statement_timeout=8000')


@app.get('/health')
def health():
    with db() as conn:
        conn.execute('SELECT review_id FROM public.reviews LIMIT 1').fetchone()
    return 'OK'


@app.get('/')
def index():
    state = search_options()
    q, bank, grade = (state[key] for key in ('q', 'bank', 'grade'))
    search = token_search(**state)
    page = max(1, min(request.args.get('page', 1, type=int), 10000))
    filters, values = [], []
    if bank:
        filters.append('r.bank_id = %s')
        values.append(bank)
    if grade:
        filters.append('r.grade = %s')
        values.append(grade)
    if search is not None:
        source, condition, params = search
        filters.append(f'EXISTS (SELECT 1 FROM {source} WHERE s.review_id = r.review_id AND {condition})')
        values.extend(params)
    elif q:
        pattern = like_pattern(q)
        filters.append("(r.title ILIKE %s ESCAPE '!' OR r.text ILIKE %s ESCAPE '!')")
        values.extend((pattern, pattern))
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    with db() as conn:
        banks = conn.execute('SELECT bank_id, name FROM public.banks ORDER BY name').fetchall()
        total = conn.execute('SELECT count(*) AS n FROM public.reviews r' + where, values).fetchone()['n']
        last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, last_page)
        rows = conn.execute('''
            SELECT r.review_id, r.title, r.text, r.grade,
                   (r.published_at AT TIME ZONE 'UTC')::date AS date_utc, b.name
            FROM public.reviews r JOIN public.banks b USING (bank_id)
        ''' + where + ' ORDER BY r.published_at DESC, r.review_id DESC LIMIT %s OFFSET %s',
            [*values, PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
        spans = defaultdict(list)
        for hit in token_matches(conn, search, [row['review_id'] for row in rows]):
            base = hit['sentence_start']
            spans[hit['review_id']].append((base + hit['start_offset'], base + hit['end_offset']))
    for row in rows:
        title_spans = literal_spans(row['title'], q) if search is None else []
        body_spans = literal_spans(row['text'], q) if search is None else sorted(spans[row['review_id']])
        row['title_parts'] = text_parts(row['title'], title_spans)
        row['excerpt_parts'] = excerpt(row['text'], body_spans)
    return render_template('index.html', banks=banks, rows=rows, total=total,
                           page=page, last_page=last_page, state=state,
                           search_modes=SEARCH_MODES, pos_labels=POS_LABELS)


@app.get('/review/<int:review_id>')
def review(review_id):
    if review_id >= 2**63:
        abort(404)
    state = search_options()
    q = state['q']
    page = max(1, min(request.args.get('page', 1, type=int), 10000))
    search = token_search(**state)
    with db() as conn:
        row = conn.execute('''
            SELECT r.title, r.text, r.grade, r.comments_count, r.sentiment,
                   (r.published_at AT TIME ZONE 'UTC')::date AS date_utc, b.name
            FROM public.reviews r JOIN public.banks b USING (bank_id)
            WHERE r.review_id = %s
        ''', [review_id]).fetchone()
        if row is None:
            abort(404)
        hits = token_matches(conn, search, [review_id])
    sentences, body_spans = {}, []
    for hit in hits:
        sentence = sentences.setdefault(hit['position'], {'text': hit['text'], 'spans': []})
        start, end, base = hit['start_offset'], hit['end_offset'], hit['sentence_start']
        sentence['spans'].append((start, end))
        body_spans.append((base + start, base + end))
    title_spans = []
    if search is None:
        title_spans = literal_spans(row['title'], q)
        body_spans = literal_spans(row['text'], q)
    return render_template('review.html', row=row, state=state, page=page,
                           title_parts=text_parts(row['title'], title_spans),
                           body_parts=text_parts(row['text'], body_spans),
                           sentences=[(position, text_parts(s['text'], s['spans']))
                                      for position, s in sentences.items()])


@app.errorhandler(psycopg.Error)
def database_error(error):
    app.logger.error('Database request failed: %s', type(error).__name__)
    return render_template('error.html', title='База временно недоступна',
                           message='Проверьте, запущен ли контейнер PostgreSQL, и попробуйте ещё раз.'), 503


@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', title='Страница не найдена',
                           message='Вернитесь к списку отзывов.'), 404
