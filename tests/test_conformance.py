"""Docs-vs-code conformance (adversarial-review P4).

Parses the machine-readable tables in the user-facing docs and asserts they have not drifted
from the code: the MCP tool set, the risk signal->level map, and the CLI subcommand set. A
single source of truth check like this would have caught several of the review's doc/code
mismatches (Findings 4, 15, and mismatch #8).

The corpus is deliberately *only* the reference docs — the README plus the pages it links as
the command/safety/provider reference. Planning documents (`roadmap.md`, `strategy.md`) are
excluded on purpose: they mention verbs they merely intend to build, and letting them satisfy
these checks would turn a conformance test into a spell-checker.
"""
import argparse
import re
from pathlib import Path

from droidjig import cli, mcp_server, risk

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: Every page a user is expected to read as reference. Missing files are an error, not a skip —
#: a renamed doc must fail loudly rather than silently shrink the corpus this test checks.
REFERENCE_PAGES = (
    ROOT / "README.md",
    DOCS / "install.md",
    DOCS / "cli-reference.md",
    DOCS / "safety.md",
    DOCS / "providers.md",
    DOCS / "daemon.md",
    DOCS / "configuration.md",
    DOCS / "macros.md",
    DOCS / "evaluation.md",
)


def _docs() -> str:
    missing = [p for p in REFERENCE_PAGES if not p.exists()]
    assert not missing, f"reference docs missing (renamed or deleted?): {missing}"
    return "\n".join(p.read_text() for p in REFERENCE_PAGES)


def test_conformance_mcp_tools_match_docs():
    text = _docs()
    # Tool-table rows only: a leading `| ` then a `phone_*` in backticks.
    documented = set(re.findall(r"^\|\s*`(phone_[a-z_]+)`\s*\|", text, re.M))
    registered = set(mcp_server.TOOLS)
    assert registered - documented == set(), \
        f"MCP tools registered but not in the docs tool table: {registered - documented}"
    assert documented - registered == set(), \
        f"Docs tool table lists tools that are not registered: {documented - registered}"
    # phone_resume must never be exposed as a tool (Finding 1).
    assert "phone_resume" not in registered


def test_conformance_risk_levels_match_docs():
    text = _docs()
    # Signal-table rows: `| `<signal>` | `<level>` |` where level is one of the four risk levels.
    documented = dict(re.findall(
        r"^\|\s*`([a-z_]+)`\s*\|\s*`(low|medium|high|critical)`\s*\|", text, re.M))
    assert documented == dict(risk._SIGNAL_LEVEL), (
        "risk signal/level drift between the docs and risk._SIGNAL_LEVEL:\n"
        f"  code = {dict(risk._SIGNAL_LEVEL)}\n  docs = {documented}")


def test_conformance_cli_subcommands_are_documented():
    parser = cli.build_parser()
    choices = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices |= set(action.choices)
    text = _docs()
    undocumented = {c for c in choices if not re.search(rf"`?droidjig {re.escape(c)}\b", text)
                    and c not in text}
    assert undocumented == set(), \
        f"CLI subcommands registered but never mentioned in the docs: {undocumented}"
