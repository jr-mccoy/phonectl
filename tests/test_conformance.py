"""Docs-vs-code conformance (adversarial-review P4).

Parses the README's machine-readable tables and asserts they have not drifted from the code:
the MCP tool set, the risk signal->level map, and the CLI subcommand set. A single source of
truth check like this would have caught several of the review's doc/code mismatches (Findings 4,
15, and mismatch #8).
"""
import argparse
import re
from pathlib import Path

from phonectl import cli, mcp_server, risk

README = Path(__file__).resolve().parent.parent / "README.md"


def _readme() -> str:
    return README.read_text()


def test_conformance_mcp_tools_match_readme():
    text = _readme()
    # Tool-table rows only: a leading `| ` then a `phone_*` in backticks.
    documented = set(re.findall(r"^\|\s*`(phone_[a-z_]+)`\s*\|", text, re.M))
    registered = set(mcp_server.TOOLS)
    assert registered - documented == set(), \
        f"MCP tools registered but not in the README tool table: {registered - documented}"
    assert documented - registered == set(), \
        f"README tool table lists tools that are not registered: {documented - registered}"
    # phone_resume must never be exposed as a tool (Finding 1).
    assert "phone_resume" not in registered


def test_conformance_risk_levels_match_readme():
    text = _readme()
    # Signal-table rows: `| `<signal>` | `<level>` |` where level is one of the four risk levels.
    documented = dict(re.findall(
        r"^\|\s*`([a-z_]+)`\s*\|\s*`(low|medium|high|critical)`\s*\|", text, re.M))
    assert documented == dict(risk._SIGNAL_LEVEL), (
        "risk signal/level drift between README and risk._SIGNAL_LEVEL:\n"
        f"  code   = {dict(risk._SIGNAL_LEVEL)}\n  readme = {documented}")


def test_conformance_cli_subcommands_are_documented():
    parser = cli.build_parser()
    choices = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices |= set(action.choices)
    text = _readme()
    undocumented = {c for c in choices if not re.search(rf"`?phonectl {re.escape(c)}\b", text)
                    and c not in text}
    assert undocumented == set(), \
        f"CLI subcommands registered but never mentioned in the README: {undocumented}"
