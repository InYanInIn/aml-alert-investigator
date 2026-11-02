CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA corpus;

CREATE TABLE corpus.documents (
    doc_id         text PRIMARY KEY,
    title          text NOT NULL,
    language       text NOT NULL,
    version        text NOT NULL,
    source_url     text NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE corpus.passages (
    passage_id     text PRIMARY KEY,
    doc_id         text NOT NULL REFERENCES corpus.documents,
    article        text NOT NULL,
    paragraph      text,
    position       integer NOT NULL,
    heading        text,
    text           text NOT NULL,
    content_sha256 text NOT NULL,
    search_config  regconfig NOT NULL,
    search         tsvector GENERATED ALWAYS AS (to_tsvector(search_config, text)) STORED,
    embedding      vector(1536),
    UNIQUE (doc_id, position)
);
CREATE INDEX passages_search ON corpus.passages USING gin (search);

ALTER TABLE core.transactions ADD COLUMN reference text;

CREATE TABLE core.reference_injections (
    injection_id text NOT NULL,
    txn_id       bigint NOT NULL REFERENCES core.transactions,
    attack       text NOT NULL,
    text         text NOT NULL,
    PRIMARY KEY (injection_id, txn_id)
);

GRANT USAGE ON SCHEMA corpus TO aml_mcp_reader, aml_app, aml_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA corpus TO aml_mcp_reader, aml_app, aml_evaluator;
GRANT SELECT ON core.reference_injections TO aml_mcp_reader, aml_evaluator;
