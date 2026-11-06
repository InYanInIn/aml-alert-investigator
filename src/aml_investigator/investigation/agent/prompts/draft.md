The regulatory passages above were retrieved for the alert's rule hits. Now write your decision.

- `recommendation`: the rule hit is why the alert exists, so it is not by itself a reason to escalate; most
  monitoring alerts turn out to be benign. `escalate` if the evidence beyond the rule hit points to
  laundering, for example funds moving on quickly through a chain or a cycle back to the account, many
  unrelated senders or receivers, or a pattern the account's own history does not explain. `close` if the
  activity fits the account's own history or has an ordinary explanation.
- `typologies`: a verdict for each typology candidate.
- `who`, `what`, `when`, `where`, `why`, `how`: short claims that follow the citation rules. `why` states the
  reasons for the recommendation and cites at least one regulatory passage.
