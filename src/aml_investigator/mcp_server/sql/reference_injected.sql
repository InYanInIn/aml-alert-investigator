COALESCE((SELECT i.text FROM core.reference_injections i WHERE i.txn_id = t.txn_id
          ORDER BY i.injection_id LIMIT 1), t.reference)
