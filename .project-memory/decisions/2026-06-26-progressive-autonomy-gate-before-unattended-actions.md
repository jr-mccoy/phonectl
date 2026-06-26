---
id: dec_20260626_progressive-autonomy-gate-before-unattended-actions
type: decision
slug: progressive-autonomy-gate-before-unattended-actions
title: Progressive autonomy gate before unattended actions
status: active
created_at: 2026-06-26T14:12:35-05:00
updated_at: 2026-06-26T14:12:35-05:00
created_by: root
agent: claude
project: phonectl
scope: project
branch: master
commit: 5557c6b
dirty_files:
  - gitignore
  - .project-memory/
confidence: high
privacy: repo-safe
review_status: unreviewed
reviewed_by: null
supersedes: []
superseded_by: null
expires_at: null
tags:
  - autonomy
  - safety
  - phase-6
evidence:
  - type: commit
    ref: 02f4e66
---

## Context
Phase 6.3: agent can run unattended; needed a safety gate so escalating actions require earned trust, not blanket permission.

## Options Considered
_(not recorded)_

## Decision
Gate unattended actions behind a progressive autonomy level; redacted memory layer keeps secrets out of agent-visible state.

## Rationale
_(not recorded)_

## Consequences
All trigger call sites must pass the unattended flag (D11 hole caught in review where it was missing).

## What Not To Retry
_(not recorded)_

## Evidence
_(not recorded)_

## Stale / Review Conditions
_(not recorded)_
