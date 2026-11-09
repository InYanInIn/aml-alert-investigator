TRUNCATE review.qa_reviews, review.decisions, review.investigations, truth.sim_actors, truth.alert_truth,
    truth.candidate_truth, truth.pattern_attempts, truth.transaction_labels, core.reference_injections,
    core.alert_rule_hits, core.alerts, core.rule_candidates, core.transactions, core.fx_rates,
    core.customer_profiles, core.accounts
RESTART IDENTITY CASCADE
