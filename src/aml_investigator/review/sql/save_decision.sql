INSERT INTO review.decisions (investigation_id, analyst, final_decision, comment)
VALUES (%s, %s, %s, %s)
ON CONFLICT (investigation_id) DO NOTHING
