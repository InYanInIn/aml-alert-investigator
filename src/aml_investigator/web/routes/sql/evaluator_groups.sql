SELECT actor.account_id, actor.group_id, actor.scenario,
       min(t.ts::date) AS first_payment_day, block.decided_on AS blocked_on
FROM truth.sim_actors actor
LEFT JOIN core.transactions t ON t.from_account = actor.account_id OR t.to_account = actor.account_id
LEFT JOIN sim.block block ON block.account_id = actor.account_id
WHERE actor.group_id IS NOT NULL
GROUP BY actor.account_id, actor.group_id, actor.scenario, block.decided_on
ORDER BY actor.group_id, actor.account_id
