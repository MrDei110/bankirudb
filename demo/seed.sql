-- All bank names and reviews below were written for this demonstration.
-- Executed only when initializing the separate demo database.
BEGIN;

INSERT INTO banks (name) VALUES
    ('Демо Банк «Север»'), ('Демо Банк «Река»'), ('Демо Банк «Маяк»');

INSERT INTO reviews (bank_id, source_path, title, text, grade, published_at, comments_count, sentiment)
SELECT b.bank_id, '/demo/review/' || x.id, x.title, x.body, x.grade,
       TIMESTAMPTZ '2026-05-01 09:00:00+00' + x.id * INTERVAL '1 day',
       x.comments, CASE WHEN x.grade >= 4 THEN 'positive' ELSE 'negative' END
FROM (VALUES
    (1, 'Север', 'Удобная оплата', 'Пользуюсь картой каждый день.', 5, 0),
    (2, 'Север', 'Плата за обслуживание', 'Заметил комиссию за обслуживание карты.', 2, 1),
    (3, 'Река', 'Перевод без доплат', 'Комиссии за перевод не было.', 5, 0),
    (4, 'Река', 'Оплата в приложении', 'Нашла карту в приложении и сразу оплатила заказ.', 4, NULL),
    (5, 'Маяк', 'Неожиданное списание', 'Комиссия оказалась выше указанной суммы.', 1, 2),
    (6, 'Маяк', 'Быстрый перевод', 'Перевод пришёл за минуту.', 5, 0)
) AS x(id, bank_name, title, body, grade, comments)
JOIN banks b ON b.name = 'Демо Банк «' || x.bank_name || '»';

-- Each synthetic review consists of one sentence.
INSERT INTO sentences (review_id, position, text, start_offset, end_offset)
SELECT review_id, 1, text, 0, char_length(text)
FROM reviews WHERE source_path LIKE '/demo/review/%';

INSERT INTO lemmas (form) VALUES
    ('комиссия'), ('карта'), ('перевод'), ('приложение');

-- Keep positions for every word/sign. Only the four demo lemmas are annotated.
WITH pieces AS (
    SELECT s.sentence_id, m.position, m.parts[2] AS surface,
           sum(char_length(m.parts[1]) + char_length(m.parts[2])) OVER (
               PARTITION BY s.sentence_id ORDER BY m.position
           )::integer AS end_offset
    FROM sentences s JOIN reviews r USING (review_id)
    CROSS JOIN LATERAL regexp_matches(
        s.text, '([[:space:]]*)([[:alnum:]]+|[^[:space:][:alnum:]])', 'g'
    ) WITH ORDINALITY AS m(parts, position)
    WHERE r.source_path LIKE '/demo/review/%'
)
INSERT INTO tokens (sentence_id, lemma_id, position, surface, part_of_speech, start_offset, end_offset)
SELECT p.sentence_id, l.lemma_id, p.position::integer, p.surface,
       CASE WHEN l.lemma_id IS NOT NULL THEN 'S' WHEN p.surface = '.' THEN 'PUNCT' END,
       p.end_offset - char_length(p.surface), p.end_offset
FROM pieces p LEFT JOIN lemmas l ON l.form = CASE
    WHEN lower(p.surface) IN ('комиссия', 'комиссии', 'комиссию') THEN 'комиссия'
    WHEN lower(p.surface) IN ('карта', 'карты', 'карту', 'картой') THEN 'карта'
    WHEN lower(p.surface) = 'перевод' THEN 'перевод'
    WHEN lower(p.surface) = 'приложении' THEN 'приложение'
END;

COMMIT;
