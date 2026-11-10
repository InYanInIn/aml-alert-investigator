INSERT INTO sim.period (period_id, first_day, last_day, target_day, system, investigation_cap,
                        status, finished_at, tape_sha256)
VALUES (0, %(first)s, %(last)s, %(last)s, 'history', 0, 'done', now(), %(hash)s)
