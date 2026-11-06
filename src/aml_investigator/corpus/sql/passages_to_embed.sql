SELECT passage_id, heading, text
FROM corpus.passages
WHERE embedding IS NULL
ORDER BY passage_id
