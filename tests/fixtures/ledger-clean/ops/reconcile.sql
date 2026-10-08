-- Invariant checks. Every query must return zero rows.

-- 1. Each transfer's entries sum to zero, and there are exactly two.
SELECT transfer_id, count(*) AS entries, sum(amount_minor) AS net
FROM ledger_entries
GROUP BY transfer_id
HAVING count(*) <> 2 OR sum(amount_minor) <> 0;

-- 2. Each balance equals the sum of its entries.
SELECT a.id, a.balance_minor, coalesce(sum(e.amount_minor), 0) AS entries_total
FROM accounts a
LEFT JOIN ledger_entries e ON e.account_id = a.id
GROUP BY a.id, a.balance_minor
HAVING a.balance_minor <> coalesce(sum(e.amount_minor), 0);
