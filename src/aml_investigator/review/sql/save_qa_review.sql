INSERT INTO review.qa_reviews (investigation_id, analyst, outcome, comment)
VALUES (%s, %s, %s, %s)
ON CONFLICT (investigation_id) DO NOTHING
