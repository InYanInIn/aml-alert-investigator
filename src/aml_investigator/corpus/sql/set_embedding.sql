UPDATE corpus.passages SET embedding = %(embedding)s::vector WHERE passage_id = %(passage_id)s
