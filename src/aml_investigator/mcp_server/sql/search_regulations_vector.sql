SELECT passage_id, doc_id, article, paragraph, heading, text, content_sha256,
       1 - (embedding <=> %(embedding)s::vector) AS rank
FROM corpus.passages
WHERE embedding IS NOT NULL
ORDER BY embedding <=> %(embedding)s::vector, passage_id
LIMIT %(top_k)s
