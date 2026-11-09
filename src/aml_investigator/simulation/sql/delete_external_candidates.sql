DELETE FROM core.rule_candidates c
USING core.accounts a
WHERE a.account_id = c.account_id
  AND a.bank_id <> %(monitored_bank_id)s
