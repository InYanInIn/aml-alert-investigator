SELECT count(*),
       count(*) FILTER (WHERE EXISTS (
           SELECT FROM laundering_sides l
           WHERE l.account_id = a.account_id AND l.ts >= a.window_start AND l.ts < a.window_end
       ))
FROM account_days a
