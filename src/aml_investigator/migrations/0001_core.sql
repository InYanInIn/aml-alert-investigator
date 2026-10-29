DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['aml_mcp_reader', 'aml_app', 'aml_evaluator'] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I LOGIN', role_name);
        END IF;
    END LOOP;
END
$$;

CREATE SCHEMA core;
CREATE SCHEMA truth;
CREATE SCHEMA review;
CREATE SCHEMA langgraph;

CREATE TABLE core.accounts (
    account_id  text PRIMARY KEY,
    bank_id     integer NOT NULL,
    bank_name   text NOT NULL,
    entity_id   text NOT NULL,
    entity_type text NOT NULL
);

CREATE TABLE core.transactions (
    txn_id            bigint PRIMARY KEY,
    ts                timestamp NOT NULL,
    from_account      text NOT NULL REFERENCES core.accounts,
    to_account        text NOT NULL REFERENCES core.accounts,
    amount_paid       numeric NOT NULL,
    paid_currency     text NOT NULL,
    amount_received   numeric NOT NULL,
    received_currency text NOT NULL,
    payment_format    text NOT NULL
);
CREATE INDEX transactions_from_ts ON core.transactions (from_account, ts);
CREATE INDEX transactions_to_ts ON core.transactions (to_account, ts);

CREATE TABLE core.alerts (
    alert_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id   text NOT NULL REFERENCES core.accounts,
    alert_date   date NOT NULL,
    window_start timestamp NOT NULL,
    window_end   timestamp NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, alert_date)
);

CREATE TABLE core.alert_rule_hits (
    alert_id         bigint NOT NULL REFERENCES core.alerts,
    rule_id          text NOT NULL,
    score            numeric NOT NULL,
    evidence_txn_ids bigint[] NOT NULL,
    PRIMARY KEY (alert_id, rule_id)
);

CREATE TABLE truth.transaction_labels (
    txn_id        bigint PRIMARY KEY REFERENCES core.transactions,
    is_laundering boolean NOT NULL
);

CREATE TABLE truth.pattern_attempts (
    attempt_id integer NOT NULL,
    typology   text NOT NULL,
    txn_id     bigint NOT NULL REFERENCES core.transactions,
    PRIMARY KEY (attempt_id, txn_id)
);

CREATE TABLE review.investigations (
    investigation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_id         bigint NOT NULL REFERENCES core.alerts,
    thread_id        text NOT NULL UNIQUE,
    model            text NOT NULL,
    prompt_version   text NOT NULL,
    recommendation   text NOT NULL CHECK (recommendation IN ('close', 'escalate')),
    decision         jsonb NOT NULL,
    verification     jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE review.decisions (
    decision_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    investigation_id bigint NOT NULL UNIQUE REFERENCES review.investigations,
    analyst          text NOT NULL,
    final_decision   text NOT NULL CHECK (final_decision IN ('close', 'escalate')),
    comment          text NOT NULL DEFAULT '',
    decided_at       timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION review.reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'review records are append-only';
END
$$;
CREATE TRIGGER investigations_append_only BEFORE UPDATE OR DELETE ON review.investigations
    FOR EACH ROW EXECUTE FUNCTION review.reject_change();
CREATE TRIGGER decisions_append_only BEFORE UPDATE OR DELETE ON review.decisions
    FOR EACH ROW EXECUTE FUNCTION review.reject_change();

GRANT USAGE ON SCHEMA core TO aml_mcp_reader, aml_app, aml_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO aml_mcp_reader, aml_app, aml_evaluator;

GRANT USAGE ON SCHEMA truth TO aml_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA truth TO aml_evaluator;

GRANT USAGE ON SCHEMA review TO aml_app, aml_evaluator;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA review TO aml_app;
GRANT SELECT ON ALL TABLES IN SCHEMA review TO aml_evaluator;

GRANT USAGE, CREATE ON SCHEMA langgraph TO aml_app;
