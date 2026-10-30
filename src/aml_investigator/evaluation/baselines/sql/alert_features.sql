WITH selected AS (
    SELECT a.alert_id, a.account_id, a.window_start, a.window_end, acc.entity_type
    FROM core.alerts a
    JOIN core.accounts acc USING (account_id)
    WHERE a.alert_id = ANY(%(alert_ids)s)
),
hits AS (
    SELECT alert_id,
           count(*) AS rule_count,
           bool_or(rule_id = 'R1_fan_in')::integer AS r1_fan_in_hit,
           bool_or(rule_id = 'R2_fan_out')::integer AS r2_fan_out_hit,
           bool_or(rule_id = 'R3_pass_through')::integer AS r3_pass_through_hit,
           bool_or(rule_id = 'R4_cycle')::integer AS r4_cycle_hit,
           bool_or(rule_id = 'R5_structuring')::integer AS r5_structuring_hit,
           bool_or(rule_id = 'R6_profile_deviation')::integer AS r6_profile_deviation_hit,
           coalesce(max(score) FILTER (WHERE rule_id = 'R1_fan_in'), 0) AS r1_fan_in_score,
           coalesce(max(score) FILTER (WHERE rule_id = 'R2_fan_out'), 0) AS r2_fan_out_score,
           coalesce(max(score) FILTER (WHERE rule_id = 'R3_pass_through'), 0) AS r3_pass_through_score,
           coalesce(max(score) FILTER (WHERE rule_id = 'R4_cycle'), 0) AS r4_cycle_score,
           coalesce(max(score) FILTER (WHERE rule_id = 'R5_structuring'), 0) AS r5_structuring_score,
           coalesce(max(score) FILTER (WHERE rule_id = 'R6_profile_deviation'), 0) AS r6_profile_deviation_score
    FROM core.alert_rule_hits
    WHERE alert_id = ANY(%(alert_ids)s)
    GROUP BY alert_id
),
flows AS (
    SELECT s.alert_id,
           true AS outgoing,
           t.from_account = t.to_account AS self_transfer,
           t.to_account AS counterparty,
           t.amount_paid_eur,
           t.payment_format,
           t.paid_currency <> t.received_currency AS cross_currency
    FROM selected s
    JOIN core.transactions t ON t.from_account = s.account_id AND t.ts >= s.window_start AND t.ts < s.window_end
    UNION ALL
    SELECT s.alert_id,
           false,
           false,
           t.from_account,
           t.amount_paid_eur,
           t.payment_format,
           t.paid_currency <> t.received_currency
    FROM selected s
    JOIN core.transactions t ON t.to_account = s.account_id AND t.ts >= s.window_start AND t.ts < s.window_end
    WHERE t.from_account <> t.to_account
),
window_activity AS (
    SELECT alert_id,
           count(*) FILTER (WHERE NOT outgoing) AS in_count,
           count(*) FILTER (WHERE outgoing AND NOT self_transfer) AS out_count,
           coalesce(sum(amount_paid_eur) FILTER (WHERE NOT outgoing), 0) AS in_eur,
           coalesce(sum(amount_paid_eur) FILTER (WHERE outgoing AND NOT self_transfer), 0) AS out_eur,
           count(DISTINCT counterparty) FILTER (WHERE NOT outgoing) AS in_counterparties,
           count(DISTINCT counterparty) FILTER (WHERE outgoing AND NOT self_transfer) AS out_counterparties,
           avg((payment_format = 'ACH')::integer) AS ach_share,
           avg((payment_format = 'Cash')::integer) AS cash_share,
           avg((payment_format = 'Bitcoin')::integer) AS bitcoin_share,
           avg(cross_currency::integer) AS cross_currency_share,
           avg(self_transfer::integer) AS self_transfer_share
    FROM flows
    GROUP BY alert_id
),
history AS (
    SELECT s.alert_id,
           least(
               (SELECT min(ts) FROM core.transactions WHERE from_account = s.account_id AND ts < s.window_end),
               (SELECT min(ts) FROM core.transactions WHERE to_account = s.account_id AND ts < s.window_end)
           ) AS first_ts
    FROM selected s
)
SELECT s.alert_id,
       coalesce(h.rule_count, 0) AS rule_count,
       coalesce(h.r1_fan_in_hit, 0) AS r1_fan_in_hit,
       coalesce(h.r2_fan_out_hit, 0) AS r2_fan_out_hit,
       coalesce(h.r3_pass_through_hit, 0) AS r3_pass_through_hit,
       coalesce(h.r4_cycle_hit, 0) AS r4_cycle_hit,
       coalesce(h.r5_structuring_hit, 0) AS r5_structuring_hit,
       coalesce(h.r6_profile_deviation_hit, 0) AS r6_profile_deviation_hit,
       coalesce(h.r1_fan_in_score, 0) AS r1_fan_in_score,
       coalesce(h.r2_fan_out_score, 0) AS r2_fan_out_score,
       coalesce(h.r3_pass_through_score, 0) AS r3_pass_through_score,
       coalesce(h.r4_cycle_score, 0) AS r4_cycle_score,
       coalesce(h.r5_structuring_score, 0) AS r5_structuring_score,
       coalesce(h.r6_profile_deviation_score, 0) AS r6_profile_deviation_score,
       (s.entity_type = 'Corporation')::integer AS entity_corporation,
       (s.entity_type = 'Partnership')::integer AS entity_partnership,
       (s.entity_type = 'Sole Proprietorship')::integer AS entity_sole_proprietorship,
       (s.entity_type = 'Individual')::integer AS entity_individual,
       (s.entity_type = 'Country')::integer AS entity_country,
       (s.entity_type = 'Direct')::integer AS entity_direct,
       coalesce(w.in_count, 0) AS in_count,
       coalesce(w.out_count, 0) AS out_count,
       coalesce(w.in_eur, 0) AS in_eur,
       coalesce(w.out_eur, 0) AS out_eur,
       coalesce(w.in_counterparties, 0) AS in_counterparties,
       coalesce(w.out_counterparties, 0) AS out_counterparties,
       coalesce(w.ach_share, 0) AS ach_share,
       coalesce(w.cash_share, 0) AS cash_share,
       coalesce(w.bitcoin_share, 0) AS bitcoin_share,
       coalesce(w.cross_currency_share, 0) AS cross_currency_share,
       coalesce(w.self_transfer_share, 0) AS self_transfer_share,
       coalesce(extract(epoch FROM s.window_end - hist.first_ts) / 86400, 0) AS account_age_days,
       CASE WHEN greatest(w.in_eur, w.out_eur) > 0
            THEN least(w.in_eur, w.out_eur) / greatest(w.in_eur, w.out_eur)
            ELSE 0
       END AS pass_through_ratio
FROM selected s
JOIN history hist USING (alert_id)
LEFT JOIN hits h USING (alert_id)
LEFT JOIN window_activity w USING (alert_id)
ORDER BY s.alert_id
