CREATE TABLE core.customer_profiles (
    entity_id            text PRIMARY KEY,
    display_name         text,
    occupation           text,
    business_description text,
    account_purpose      text,
    expected_activity    text,
    declared_monthly_eur numeric,
    customer_since       date
);

CREATE TABLE truth.sim_actors (
    account_id text PRIMARY KEY REFERENCES core.accounts,
    persona    text NOT NULL,
    group_id   integer,
    scenario   text
);

GRANT SELECT ON core.customer_profiles TO aml_mcp_reader, aml_app, aml_evaluator;
GRANT SELECT ON truth.sim_actors TO aml_evaluator;
