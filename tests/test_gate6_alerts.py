from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forex_ai.runtime.alerts import build_alert, deliver_alert, retain_files, spool_alert


def test_spool_alert_is_atomic_json(tmp_path):
    alert = build_alert(severity="CRITICAL", code="DB BAD", summary="db failed", context={"x": 1})
    path = spool_alert(alert, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["code"] == "DB BAD"
    assert payload["context"] == {"x": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_deliver_alert_uses_fixed_executable_and_stdin(tmp_path):
    sink = tmp_path / "sink.json"
    exe = tmp_path / "deliver.sh"
    exe.write_text(f"#!/bin/sh\ncat > {sink}\n", encoding="utf-8")
    exe.chmod(0o700)
    alert = build_alert(severity="WARN", code="TEST", summary="hello")
    assert deliver_alert(alert, executable=str(exe))
    assert json.loads(sink.read_text(encoding="utf-8"))["code"] == "TEST"


def test_no_transport_returns_false():
    alert = build_alert(severity="WARN", code="TEST", summary="hello")
    assert deliver_alert(alert, executable=None) is False


def test_retention_keeps_newest_files(tmp_path):
    files=[]
    for idx in range(4):
        p=tmp_path/f"forex-{idx}.db";p.write_text(str(idx),encoding="utf-8");os.utime(p,(idx+1,idx+1));files.append(p)
    removed=retain_files(tmp_path,keep=2,patterns=("forex-*.db",))
    assert set(removed)==set(files[:2])
    assert files[2].exists() and files[3].exists()


def test_retention_rejects_zero(tmp_path):
    with pytest.raises(ValueError):
        retain_files(tmp_path,keep=0)
