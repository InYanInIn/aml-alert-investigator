INSERT INTO corpus.passages (passage_id, doc_id, article, paragraph, position, heading, text, content_sha256, search_config)
VALUES (
    %(passage_id)s, %(doc_id)s, %(article)s, %(paragraph)s, %(position)s, %(heading)s, %(text)s, %(content_sha256)s,
    %(search_config)s::regconfig
)
