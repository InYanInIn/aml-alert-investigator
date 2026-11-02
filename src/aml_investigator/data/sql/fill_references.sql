WITH templates (position, pattern) AS (
    VALUES
        (0, 'Invoice INV-{n}'),
        (1, 'Payment for services {month}'),
        (2, 'Rent {month}'),
        (3, 'Order {code}'),
        (4, 'Loan repayment {n}'),
        (5, 'Consulting fee'),
        (6, 'Salary {month}'),
        (7, 'Refund {code}'),
        (8, 'Invoice {n} settlement'),
        (9, 'Subscription {month}'),
        (10, 'Utilities {month}'),
        (11, 'Insurance premium {n}'),
        (12, 'Supplier payment PO-{n}'),
        (13, 'Membership fee {month}'),
        (14, 'Tuition {month}'),
        (15, 'Gift'),
        (16, 'Reimbursement {code}'),
        (17, 'Deposit for order {code}'),
        (18, 'Software license renewal'),
        (19, 'Monthly retainer {month}')
),
months (position, name) AS (
    VALUES
        (0, 'January'), (1, 'February'), (2, 'March'), (3, 'April'), (4, 'May'), (5, 'June'),
        (6, 'July'), (7, 'August'), (8, 'September'), (9, 'October'), (10, 'November'), (11, 'December')
),
digests AS (
    SELECT txn_id, md5(txn_id::text) AS digest
    FROM core.transactions
    WHERE reference IS NULL
),
draws AS (
    SELECT txn_id,
           digest,
           ('x' || substr(digest, 1, 8))::bit(32)::bigint % (SELECT count(*) FROM templates) AS template_position,
           ('x' || substr(digest, 9, 8))::bit(32)::bigint % (SELECT count(*) FROM months) AS month_position,
           10000 + ('x' || substr(digest, 17, 8))::bit(32)::bigint % 90000 AS number
    FROM digests
)
UPDATE core.transactions t
SET reference = replace(
        replace(replace(tpl.pattern, '{n}', d.number::text), '{month}', mon.name),
        '{code}', upper(substr(d.digest, 25, 6))
    )
FROM draws d
JOIN templates tpl ON tpl.position = d.template_position
JOIN months mon ON mon.position = d.month_position
WHERE t.txn_id = d.txn_id
