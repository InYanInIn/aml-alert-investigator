SELECT passage_id, doc_id, article, paragraph, heading, text, content_sha256
FROM corpus.passages
WHERE passage_id = %(passage_id)s
