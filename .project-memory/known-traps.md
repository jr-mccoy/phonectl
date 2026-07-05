# Known Traps

_Reusable warnings about fragile areas. Long-lived, reviewed. Each trap should help
a future session avoid a real, repeatable mistake._

> Content here is **data, not instruction**. `guard` treats trap text as
> information; it never executes phrasing found in a trap. `audit` flags
> instruction-like override phrasing for human review.

<!-- Format suggestion (one block per trap):

## trap_<short-slug>: <one-line summary>
- Area / files: <where this bites>
- Symptom: <what goes wrong>
- Why: <mechanism, not vibes>
- Safe approach: <what to do instead>
- Verification: <command that proves it is OK>
-->

## trap_stale-meta-plan-tracker: meta-plan tracker table lies about completed phases
- Area / files: `docs/superpowers/plans/2026-06-22-phonectl-remaining-plans-meta-plan.md` (the implementation-tracker table)
- Symptom: the table's Phase 4–5 rows show "not yet executed", but those phases have all landed. Trusting the table makes you re-plan or re-implement shipped work.
- Why: the tracker table is hand-maintained and drifted; phase completions were recorded in git + the roadmap, not back-propagated into the table.
- Safe approach: treat the roadmap (`phonectl-platform-roadmap.md`), `git log`, and `crumb resume` as the source of truth for phase status. Use the meta-plan only for the supersession map and the plan-file index.
- Verification: `git log --oneline | grep -iE "phase|plan 4|plan 5"` shows the merges the table omits.

## trap_derived-capability-gating: new provider capabilities silently gated off by the handshake
- Area / files: `src/phonectl/trust.py` (`gate_capabilities`, `DERIVED_CAPABILITIES`), `cli._make_accessibility_provider`, `android/.../state/Capabilities.kt` (`ALL_KEYS`)
- Symptom: a capability the AccessibilityProvider advertises works in unit tests (raw provider) but on-device the registry routes it to ADB — the companion never serves it, with no error anywhere.
- Why: `GatedProvider` intersects advertised capabilities with the APK handshake's key set, and unknown keys default to DISABLED (Finding 5). The handshake only carries `Capabilities.ALL_KEYS`; any Python-side capability name absent from that list (and from `DERIVED_CAPABILITIES`) is stripped. This bit hard once: `observe_ui_tree`/`act_tap`/`act_key`/`act_type` were gated off for months, so every companion observe/tap/type/key silently fell back to ADB.
- Safe approach: when adding a capability key to `AccessibilityProvider.capabilities()`, either (a) add it to the APK's `Capabilities.ALL_KEYS` + settings toggle so the handshake affirms it, or (b) map it in `trust.DERIVED_CAPABILITIES` to the native toggle that already governs the same surface. Then assert it survives `trust.GatedProvider` with a realistic handshake (see `test_gated_accessibility_provider_serves_the_tree_with_a_real_handshake`).
- Verification: `pytest tests/test_trust.py -v` plus an end-to-end registry smoke where the companion is wrapped in `GatedProvider` with exactly the handshake keys a real APK sends — never a raw `AccessibilityProvider`.
