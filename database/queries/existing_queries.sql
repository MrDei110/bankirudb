/*
-- Из import_reviews.py: добавление банков и отзывов.
INSERT INTO banks (name)
SELECT DISTINCT bank FROM incoming
ON CONFLICT (name) DO NOTHING;

INSERT INTO reviews (bank_id,source_path,title,text,grade,published_at,comments_count,sentiment)
SELECT b.bank_id,i.source_path,i.title,i.text,i.grade,i.published_at,i.comments_count,i.sentiment
FROM incoming i JOIN banks b ON b.name=i.bank
ON CONFLICT (source_path) DO NOTHING;

-- Из import_annotation.py: добавление лемм, предложений и токенов.
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
*/

-- Проверка подключения.
SELECT current_database() AS database_name,
       current_user AS user_name;

-- Подсчёт записей: запрос из переписки при проверке загрузки.
SELECT 'banks' AS table_name, COUNT(*) AS rows_count FROM public.banks
UNION ALL
SELECT 'reviews', COUNT(*) FROM public.reviews
UNION ALL
SELECT 'sentences', COUNT(*) FROM public.sentences
UNION ALL
SELECT 'lemmas', COUNT(*) FROM public.lemmas
UNION ALL
SELECT 'tokens', COUNT(*) FROM public.tokens;

-- Частые леммы существительных: запрос из переписки после импорта.
SELECT l.form AS lemma, COUNT(*) AS occurrences
FROM tokens t
JOIN lemmas l ON l.lemma_id = t.lemma_id
WHERE t.part_of_speech = 'S'
GROUP BY l.lemma_id, l.form
ORDER BY occurrences DESC, l.form
LIMIT 20;
