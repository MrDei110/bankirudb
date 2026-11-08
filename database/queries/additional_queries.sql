-- RIGHT JOIN: все банки
SELECT b.bank_id, b.name AS bank_name, COUNT(r.review_id) AS reviews_count
FROM reviews r
RIGHT JOIN banks b ON b.bank_id = r.bank_id
GROUP BY b.bank_id, b.name
ORDER BY reviews_count DESC, b.name;

-- Конкатенация: объединение строк через ||
SELECT r.review_id,
       r.title || ' — оценка: ' || CAST(r.grade AS TEXT) AS review_description
FROM reviews r
ORDER BY r.review_id
LIMIT 50;

-- Простая выборка конкретных полей с псевдонимом таблицы
SELECT r.review_id, r.title, r.grade, r.published_at
FROM reviews AS r
LIMIT 50;

-- r.* — все столбцы таблицы
SELECT r.*
FROM reviews AS r
LIMIT 50;

-- Несколько ключей сортировки: оценка по возрастанию,
-- внутри оценки сначала новые отзывы; идентификатор разрешает совпадения
SELECT r.review_id, r.title, r.grade, r.published_at
FROM reviews r
ORDER BY r.grade ASC, r.published_at DESC, r.review_id
LIMIT 20;

-- DISTINCT: только разные значения части речи
SELECT DISTINCT t.part_of_speech
FROM tokens t
WHERE t.part_of_speech IS NOT NULL
ORDER BY t.part_of_speech;

-- Три вида COUNT и COUNT по необязательному полю.
-- COUNT(*) считает все отзывы; COUNT(поле) пропускает NULL.
-- COUNT(DISTINCT bank_id) считает разные банки, представленные в отзывах.
-- Ноль комментариев — известное значение и учитывается COUNT(comments_count).
SELECT COUNT(*) AS all_reviews,
       COUNT(r.bank_id) AS reviews_with_bank,
       COUNT(DISTINCT r.bank_id) AS unique_banks,
       COUNT(r.comments_count) AS reviews_with_known_comment_count
FROM reviews r;

-- Фильтрация по дате
SELECT r.review_id, r.title, r.published_at
FROM reviews r
WHERE r.published_at > TIMESTAMPTZ '2026-05-01 00:00:00+00'
ORDER BY r.published_at, r.review_id
LIMIT 20;

-- IS NULL: токены, у которых нет леммы
SELECT t.token_id, t.surface, t.part_of_speech
FROM tokens t
WHERE t.lemma_id IS NULL
ORDER BY t.token_id
LIMIT 30;

-- LIKE: заголовок содержит «карт». % означает любое число символов
SELECT r.review_id, r.title, r.grade
FROM reviews r
WHERE lower(r.title) LIKE '%карт%'
ORDER BY r.review_id
LIMIT 20;

-- AND / OR: низкие оценки с упоминанием комиссии ИЛИ
-- высокие оценки с упоминанием кешбэка
SELECT r.review_id, r.title, r.grade
FROM reviews r
WHERE (r.grade <= 2 AND lower(r.text) LIKE '%комисси%')
   OR (r.grade >= 4 AND (lower(r.text) LIKE '%кэшбэк%'
                        OR lower(r.text) LIKE '%кешбэк%'))
ORDER BY r.grade, r.review_id
LIMIT 30;

-- Вывод строки, конкатенация и псевдоним результата (Hello, World)
SELECT 'База отзывов: ' || current_database() AS project_title;

-- версия сервера
SELECT version() AS server_version;

-- Извлечение частей строки версии.
-- split_part(..., ' ', 2) возвращает номер версии целиком
SELECT split_part(version(), ' ', 1) AS dbms,
       split_part(version(), ' ', 2) AS version_number,
       substring(version() FROM '[0-9]+') AS major_version;

-- Текущий момент: исходное значение, преобразование в строку,
-- форматирование даты и даты/часа
SELECT now() AS current_moment,
       CAST(now() AS VARCHAR(20)) AS moment_as_text,
       to_char(now(), 'DD.MM.YYYY') AS formatted_date,
       to_char(now(), 'YYYY-MM: HH24') AS formatted_month_hour;

-- Форматирование даты из данных, в UTC
SELECT r.review_id,
       r.title,
       to_char(r.published_at AT TIME ZONE 'UTC', 'DD.MM.YYYY HH24:MI') AS published_utc
FROM reviews r
ORDER BY r.published_at DESC, r.review_id DESC
LIMIT 10;

-- Конкатенация с текущим годом
SELECT 'База отзывов Banki.ru, ' || to_char(now(), 'YYYY') || ' год' AS title;
