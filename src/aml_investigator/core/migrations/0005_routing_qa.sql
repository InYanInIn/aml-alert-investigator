ALTER TABLE review.investigations
    ADD COLUMN route text NOT NULL DEFAULT 'escalated' CHECK (route IN ('auto_closed', 'qa_sample', 'escalated')),
    ADD COLUMN trace jsonb NOT NULL DEFAULT '{}';
ALTER TABLE review.investigations ALTER COLUMN route DROP DEFAULT, ALTER COLUMN trace DROP DEFAULT;

CREATE TABLE review.qa_reviews (
    qa_review_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    investigation_id bigint NOT NULL UNIQUE REFERENCES review.investigations,
    analyst          text NOT NULL,
    outcome          text NOT NULL CHECK (outcome IN ('confirmed', 'reopened')),
    comment          text NOT NULL DEFAULT '',
    reviewed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER qa_reviews_append_only BEFORE UPDATE OR DELETE ON review.qa_reviews
    FOR EACH ROW EXECUTE FUNCTION review.reject_change();

GRANT SELECT, INSERT ON review.qa_reviews TO aml_app;
GRANT SELECT ON review.qa_reviews TO aml_evaluator;
