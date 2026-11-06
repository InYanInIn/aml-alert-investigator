SELECT passage_id, doc_id, article, paragraph, heading, text, content_sha256,
       1 - (embedding <=> %(embedding)s::vector) AS rank
FROM corpus.passages
WHERE embedding IS NOT NULL AND (%(doc_id)s::text IS NULL OR doc_id = %(doc_id)s)
ORDER BY embedding <=> %(embedding)s::vector, passage_id
LIMIT %(top_k)s
