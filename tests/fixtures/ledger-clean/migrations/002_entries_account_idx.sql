-- Entry history is read per account, newest first (keyset on id).
CREATE INDEX CONCURRENTLY IF NOT EXISTS ledger_entries_account_id_idx
    ON ledger_entries (account_id, id DESC);
