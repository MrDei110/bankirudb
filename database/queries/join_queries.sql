-- отзывы вместе с названиями банков
SELECT r.review_id, b.name AS bank_name, r.title, r.grade
FROM reviews r
INNER JOIN banks b ON b.bank_id = r.bank_id
ORDER BY r.review_id
LIMIT 20;

-- предложения и заголовки отзывов, из которых они взяты.
SELECT r.review_id, r.title, s.position AS sentence_number, s.text AS sentence_text
FROM reviews r
INNER JOIN sentences s ON s.review_id = r.review_id
ORDER BY r.review_id, s.position
LIMIT 30;

-- токены вместе с леммами, включая токены без леммы
SELECT t.token_id, t.surface, l.form AS lemma, t.part_of_speech
FROM tokens t
LEFT JOIN lemmas l ON l.lemma_id = t.lemma_id
ORDER BY t.token_id
LIMIT 50;

-- Все пять таблиц: банк -> отзыв -> предложение -> токен -> лемма
SELECT b.name AS bank_name,
       r.review_id,
       r.title,
       s.position AS sentence_number,
       s.text AS sentence_text,
       t.position AS token_number,
       t.surface,
       l.form AS lemma,
       t.part_of_speech
FROM banks b
INNER JOIN reviews r ON r.bank_id = b.bank_id
INNER JOIN sentences s ON s.review_id = r.review_id
INNER JOIN tokens t ON t.sentence_id = s.sentence_id
LEFT JOIN lemmas l ON l.lemma_id = t.lemma_id
ORDER BY r.review_id, s.position, t.position
LIMIT 50;

-- все банки и количество их отзывов
SELECT b.bank_id, b.name AS bank_name, COUNT(r.review_id) AS reviews_count
FROM banks b
LEFT JOIN reviews r ON r.bank_id = b.bank_id
GROUP BY b.bank_id, b.name
ORDER BY reviews_count DESC, b.name;

-- предложения с леммой «комиссия»
SELECT DISTINCT b.name AS bank_name, r.review_id, s.position, s.text AS sentence_text
FROM banks b
INNER JOIN reviews r ON r.bank_id = b.bank_id
INNER JOIN sentences s ON s.review_id = r.review_id
INNER JOIN tokens t ON t.sentence_id = s.sentence_id
INNER JOIN lemmas l ON l.lemma_id = t.lemma_id
WHERE l.form = 'комиссия'
ORDER BY r.review_id, s.position
LIMIT 30;
