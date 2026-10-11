import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("audit_gate", Path(__file__).parents[2] / "scripts/check_dependency_audit.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_pip_findings_are_not_scanner_failure():
    data = json.dumps({"dependencies": [{"name": "example", "vulns": [{"id": "TEST"}]}]})
    assert gate.check("pip", data, 1)[0] is False


def test_pip_clean_completed_scan():
    assert gate.check("pip", json.dumps({"dependencies": [{"vulns": []}]}), 0)[0] is True


@pytest.mark.parametrize("content,status", [("{}", 0), ("", 2),
    ('{"dependencies":[{"skip_reason":"unresolved"}]}', 0), ('{"dependencies":[{"vulns":[]}]}', 2)])
def test_broken_pip_scan_never_passes(content, status):
    with pytest.raises(ValueError):
        gate.check("pip", content, status)


def yarn_summary(**counts):
    return json.dumps({"type": "auditSummary", "data": {"vulnerabilities": {
        "info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, **counts}}})


def test_yarn_gates_high_critical_not_lower_severity():
    assert gate.check("yarn", yarn_summary(high=2), 8)[0] is False
    assert gate.check("yarn", yarn_summary(critical=1), 16)[0] is False
    assert gate.check("yarn", yarn_summary(low=3, moderate=1), 6)[0] is True


@pytest.mark.parametrize("content,status", [("", 0), ('{"type":"error"}', 1), (yarn_summary(), 1)])
def test_yarn_incomplete_or_inconsistent_scan_never_passes(content, status):
    with pytest.raises(ValueError):
        gate.check("yarn", content, status)


def yarn_advisory(module, severity, path):
    return json.dumps({"type": "auditAdvisory", "data": {
        "resolution": {"path": path, "dev": False},
        "advisory": {"module_name": module, "severity": severity}}})


def test_yarn_build_only_high_does_not_block_but_runtime_does():
    build = "\n".join([yarn_advisory("nth-check", "high", "react-scripts>@svgr/webpack>nth-check"),
                       yarn_advisory("tar", "critical", "@capacitor/cli>tar"),
                       yarn_summary(high=1, critical=1)])
    ok, msg = gate.check("yarn", build, 24)
    assert ok is True and "derleme" in msg
    mixed = "\n".join([yarn_advisory("nth-check", "high", "react-scripts>@svgr/webpack>nth-check"),
                       yarn_advisory("axios", "high", "axios"),
                       yarn_summary(high=2)])
    ok, msg = gate.check("yarn", mixed, 8)
    assert ok is False and "axios" in msg


def test_yarn_moderate_runtime_advisory_does_not_block():
    content = "\n".join([yarn_advisory("follow-redirects", "moderate", "axios>follow-redirects"),
                         yarn_summary(moderate=1)])
    assert gate.check("yarn", content, 4)[0] is True
