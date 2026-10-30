CREATE TABLE core.fx_rates (
    currency     text PRIMARY KEY,
    eur_per_unit numeric NOT NULL CHECK (eur_per_unit > 0)
);

ALTER TABLE core.transactions ADD COLUMN amount_paid_eur numeric NOT NULL;

CREATE TABLE core.rule_candidates (
    rule_id          text NOT NULL,
    account_id       text NOT NULL REFERENCES core.accounts,
    alert_date       date NOT NULL,
    window_start     timestamp NOT NULL,
    window_end       timestamp NOT NULL,
    score            numeric NOT NULL,
    evidence_txn_ids bigint[] NOT NULL,
    PRIMARY KEY (rule_id, account_id, alert_date)
);
CREATE INDEX rule_candidates_rule_score ON core.rule_candidates (rule_id, alert_date, score);

CREATE TABLE truth.alert_truth (
    alert_id                     bigint PRIMARY KEY REFERENCES core.alerts,
    escalate_truth               boolean NOT NULL,
    escalate_truth_account_level boolean NOT NULL,
    typologies                   text[] NOT NULL
);

GRANT SELECT ON core.fx_rates, core.rule_candidates TO aml_mcp_reader, aml_app, aml_evaluator;
GRANT SELECT ON truth.alert_truth TO aml_evaluator;
