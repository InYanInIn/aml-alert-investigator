INSERT INTO corpus.documents (doc_id, title, language, version, source_url, content_sha256)
VALUES (%(doc_id)s, %(title)s, %(language)s, %(version)s, %(source_url)s, %(content_sha256)s)
ON CONFLICT (doc_id) DO UPDATE
SET title = EXCLUDED.title,
    language = EXCLUDED.language,
    version = EXCLUDED.version,
    source_url = EXCLUDED.source_url,
    content_sha256 = EXCLUDED.content_sha256
