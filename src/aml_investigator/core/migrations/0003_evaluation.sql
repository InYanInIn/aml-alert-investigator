CREATE TABLE truth.candidate_truth (
    rule_id        text NOT NULL,
    account_id     text NOT NULL,
    alert_date     date NOT NULL,
    has_laundering boolean NOT NULL,
    PRIMARY KEY (rule_id, account_id, alert_date)
);

GRANT SELECT ON truth.candidate_truth TO aml_evaluator;
