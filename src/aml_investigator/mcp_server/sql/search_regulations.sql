SELECT passage_id, doc_id, article, paragraph, heading, text, rank
FROM (
    SELECT p.*, ts_rank_cd(p.search, websearch_to_tsquery(p.search_config, %(query)s)) AS rank
    FROM corpus.passages p
    WHERE p.search @@ websearch_to_tsquery(p.search_config, %(query)s)
) matches
ORDER BY rank DESC, passage_id
LIMIT %(top_k)s
