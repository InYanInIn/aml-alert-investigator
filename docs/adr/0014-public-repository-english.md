# ADR-0014: Public repository from the first commit; English UI and docs

Status: accepted · 2026-10-28

## Context
The repository is a CV artefact for banks and fintechs, so reviewers must be able to read its history. A public
repository makes every commit permanent and visible.

## Decision
- Public GitHub repository from M0; docs, code, UI and commit messages in English.
- Committed: code, docs, frozen eval sample IDs, eval result JSON, recorded model responses (synthetic data
  only), regulatory texts with source acknowledgement.
- Never committed: Kaggle files (`data/raw/` is git-ignored; the README links the dataset), `.env`, Terraform
  state, any secret.
- GitHub secret scanning and push protection enabled on the repository.

## Consequences
- A leaked secret in history would be public immediately; push protection is the guard, rotation the remedy.
- Anyone can reproduce results after downloading the dataset themselves.
