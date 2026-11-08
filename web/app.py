import os
from flask import Flask, abort, render_template, request
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.config['DEMO_DATA'] = os.environ.get('BANKIRU_DEMO') == '1'
PAGE_SIZE = 12


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
    q = request.args.get('q', '').strip()[:100]
    mode = request.args.get('mode', 'text')
    mode = mode if mode in ('text', 'lemma') else 'text'
    bank = request.args.get('bank', 0, type=int)
    grade = request.args.get('grade', 0, type=int)
    bank = bank if 0 < bank < 2**63 else 0
    grade = grade if 1 <= grade <= 5 else 0
    page = max(1, min(request.args.get('page', 1, type=int), 10000))
    filters, values = [], []
    if bank:
        filters.append('r.bank_id = %s')
        values.append(bank)
    if grade:
        filters.append('r.grade = %s')
        values.append(grade)
    if q and mode == 'text':
        pattern = '%' + q.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        filters.append("(r.title ILIKE %s ESCAPE '!' OR r.text ILIKE %s ESCAPE '!')")
        values.extend((pattern, pattern))
    elif q:
        filters.append('''EXISTS (
            SELECT 1 FROM public.sentences s
            JOIN public.tokens t USING (sentence_id)
            JOIN public.lemmas l USING (lemma_id)
            WHERE s.review_id = r.review_id AND l.form = lower(%s)
        )''')
        values.append(q)
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    with db() as conn:
        banks = conn.execute('SELECT bank_id, name FROM public.banks ORDER BY name').fetchall()
        total = conn.execute('SELECT count(*) AS n FROM public.reviews r' + where, values).fetchone()['n']
        last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, last_page)
        rows = conn.execute('''
            SELECT r.review_id, r.title, left(r.text, 230) AS excerpt, r.grade,
                   (r.published_at AT TIME ZONE 'UTC')::date AS date_utc, b.name
            FROM public.reviews r JOIN public.banks b USING (bank_id)
        ''' + where + ' ORDER BY r.published_at DESC, r.review_id DESC LIMIT %s OFFSET %s',
            [*values, PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    return render_template('index.html', banks=banks, rows=rows, total=total,
                           page=page, last_page=last_page, q=q, mode=mode, bank=bank, grade=grade)


@app.get('/review/<int:review_id>')
def review(review_id):
    if review_id >= 2**63:
        abort(404)
    lemma = request.args.get('lemma', '').strip()[:100]
    with db() as conn:
        row = conn.execute('''
            SELECT r.title, r.text, r.grade, r.comments_count, r.sentiment,
                   (r.published_at AT TIME ZONE 'UTC')::date AS date_utc, b.name
            FROM public.reviews r JOIN public.banks b USING (bank_id)
            WHERE r.review_id = %s
        ''', [review_id]).fetchone()
        if row is None:
            abort(404)
        sentences = []
        if lemma:
            sentences = conn.execute('''
                SELECT s.position, s.text FROM public.sentences s
                WHERE s.review_id = %s AND EXISTS (
                    SELECT 1 FROM public.tokens t JOIN public.lemmas l USING (lemma_id)
                    WHERE t.sentence_id = s.sentence_id AND l.form = lower(%s)
                ) ORDER BY s.position
            ''', [review_id, lemma]).fetchall()
    return render_template('review.html', row=row, lemma=lemma, sentences=sentences)


@app.errorhandler(psycopg.Error)
def database_error(error):
    app.logger.error('Database request failed: %s', type(error).__name__)
    return render_template('error.html', title='База временно недоступна',
                           message='Проверьте, запущен ли контейнер PostgreSQL, и попробуйте ещё раз.'), 503


@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', title='Страница не найдена',
                           message='Вернитесь к списку отзывов.'), 404
