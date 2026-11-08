-- Banki.ru reviews: PostgreSQL schema, intended for TDM DDL import.
-- Run once in an empty database. No database creation or data import included.
-- Offsets count Unicode characters, not bytes; intervals are [start, end).
-- Sentence offsets refer to reviews.text. Token offsets refer to sentences.text.

CREATE TABLE banks (
    bank_id BIGSERIAL NOT NULL,
    name VARCHAR(255) NOT NULL,
    CONSTRAINT pk_banks PRIMARY KEY (bank_id),
    CONSTRAINT uq_banks_name UNIQUE (name),
    CONSTRAINT ck_banks_name CHECK (length(btrim(name)) > 0)
);

CREATE TABLE reviews (
    review_id BIGSERIAL NOT NULL,
    bank_id BIGINT NOT NULL,
    source_path VARCHAR(512) NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    grade SMALLINT NOT NULL,
    published_at TIMESTAMP WITH TIME ZONE NOT NULL,
    comments_count INTEGER,
    sentiment VARCHAR(16) NOT NULL,
    CONSTRAINT pk_reviews PRIMARY KEY (review_id),
    CONSTRAINT fk_reviews_bank FOREIGN KEY (bank_id) REFERENCES banks (bank_id),
    CONSTRAINT uq_reviews_source_path UNIQUE (source_path),
    CONSTRAINT ck_reviews_source_path CHECK (length(btrim(source_path)) > 0),
    CONSTRAINT ck_reviews_title CHECK (length(btrim(title)) > 0),
    CONSTRAINT ck_reviews_text CHECK (length(btrim(text)) > 0),
    CONSTRAINT ck_reviews_grade CHECK (grade BETWEEN 1 AND 5),
    CONSTRAINT ck_reviews_comments CHECK (comments_count >= 0),
    CONSTRAINT ck_reviews_sentiment CHECK (sentiment IN ('positive', 'negative'))
);

CREATE TABLE sentences (
    sentence_id BIGSERIAL NOT NULL,
    review_id BIGINT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    CONSTRAINT pk_sentences PRIMARY KEY (sentence_id),
    CONSTRAINT fk_sentences_review FOREIGN KEY (review_id)
        REFERENCES reviews (review_id) ON DELETE CASCADE,
    CONSTRAINT uq_sentences_position UNIQUE (review_id, position),
    CONSTRAINT ck_sentences_position CHECK (position >= 1),
    CONSTRAINT ck_sentences_text CHECK (length(btrim(text)) > 0),
    CONSTRAINT ck_sentences_offsets CHECK (
        start_offset >= 0 AND end_offset > start_offset
        AND end_offset - start_offset = char_length(text)
    )
);

CREATE TABLE lemmas (
    lemma_id BIGSERIAL NOT NULL,
    form VARCHAR(255) NOT NULL,
    CONSTRAINT pk_lemmas PRIMARY KEY (lemma_id),
    CONSTRAINT uq_lemmas_form UNIQUE (form),
    CONSTRAINT ck_lemmas_form CHECK (length(btrim(form)) > 0)
);

CREATE TABLE tokens (
    token_id BIGSERIAL NOT NULL,
    sentence_id BIGINT NOT NULL,
    lemma_id BIGINT,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL,
    part_of_speech VARCHAR(32),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    CONSTRAINT pk_tokens PRIMARY KEY (token_id),
    CONSTRAINT fk_tokens_sentence FOREIGN KEY (sentence_id)
        REFERENCES sentences (sentence_id) ON DELETE CASCADE,
    CONSTRAINT fk_tokens_lemma FOREIGN KEY (lemma_id) REFERENCES lemmas (lemma_id),
    CONSTRAINT uq_tokens_position UNIQUE (sentence_id, position),
    CONSTRAINT ck_tokens_position CHECK (position >= 1),
    CONSTRAINT ck_tokens_surface CHECK (length(btrim(surface)) > 0),
    CONSTRAINT ck_tokens_pos CHECK (
        part_of_speech IS NULL OR length(btrim(part_of_speech)) > 0
    ),
    CONSTRAINT ck_tokens_offsets CHECK (
        start_offset >= 0 AND end_offset > start_offset
        AND end_offset - start_offset = char_length(surface)
    )
);

-- UNIQUE constraints already index sentence and token positions within parents.
CREATE INDEX ix_reviews_bank_date ON reviews (bank_id, published_at);
CREATE INDEX ix_tokens_lemma ON tokens (lemma_id);

-- Matching offsets to the parent text and preventing overlapping spans must
-- be validated during text processing/import. CHECK cannot read another table.
-- Text must not be normalized after offsets are calculated.
