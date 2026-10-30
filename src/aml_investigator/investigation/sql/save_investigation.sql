WITH inserted AS (
    INSERT INTO review.investigations
        (alert_id, thread_id, model, prompt_version, recommendation, decision, verification)
    VALUES (%(alert_id)s, %(thread_id)s, %(model)s, %(prompt_version)s, %(recommendation)s,
            %(decision)s, %(verification)s)
    ON CONFLICT (thread_id) DO NOTHING
    RETURNING investigation_id
)
SELECT investigation_id FROM inserted
UNION ALL
SELECT investigation_id FROM review.investigations WHERE thread_id = %(thread_id)s
