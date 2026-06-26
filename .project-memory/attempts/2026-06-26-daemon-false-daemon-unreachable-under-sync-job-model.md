---
id: att_20260626_daemon-false-daemon-unreachable-under-sync-job-model
type: attempt
slug: daemon-false-daemon-unreachable-under-sync-job-model
title: Daemon false 'daemon_unreachable' under sync job model
status: active
created_at: 2026-06-26T14:13:05-05:00
updated_at: 2026-06-26T14:13:05-05:00
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
  - daemon
  - reliability
  - phase-6
evidence:
  - type: commit
    ref: f08ce9d
---

## Problem
Long-running phone actions blocked the daemon, producing false daemon_unreachable errors.

## Tried
Switched to an async job model with TTL eviction on the idempotency cache.

## Result
Fixed; live device re-validation PASSED 2026-06-23.

## Why It Failed / Succeeded
Sync execution held the RPC thread; async decoupled job lifetime from the request.

## Do Not Retry Unless
A simpler sync path is proven non-blocking under real device latency.

## Evidence
_(not recorded)_

## Related Records
_(not recorded)_
