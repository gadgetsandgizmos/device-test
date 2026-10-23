import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from device_test import expect


def test_rules_pass_and_fail():
    results = {"delivery.proven_ratio": 0.964, "stations.rebooted": 0}
    verdicts = expect.judge({"delivery.proven_ratio": ">= 0.85",
                             "stations.rebooted": "== 0"}, results)
    assert [v["ok"] for v in verdicts] == [True, True]
    verdicts = expect.judge({"delivery.proven_ratio": ">=0.97"}, results)
    assert verdicts[0]["ok"] is False
    assert expect.describe(verdicts[0]) == "delivery.proven_ratio 0.964, expected >= 0.97"


def test_missing_metric_fails():
    v, = expect.judge({"latency.proven_s.p90": "<= 900"}, {"delivery.sent": 720})
    assert v["ok"] is False
    assert expect.describe(v) == "latency.proven_s.p90 not reported, expected <= 900"
    v, = expect.judge({"delivery.sent": "> 0"}, None)
    assert v["ok"] is False


def test_bad_rule_is_an_error():
    with pytest.raises(expect.ExpectError):
        expect.judge({"delivery.sent": "about 720"}, {"delivery.sent": 720})


def test_non_numeric_value_fails():
    v, = expect.judge({"run.firmware": "> 1"}, {"run.firmware": "abc"})
    assert v["ok"] is False


def test_load(tmp_path):
    assert expect.load(tmp_path / "results.json") is None
    (tmp_path / "results.json").write_text('{"a": 1}')
    assert expect.load(tmp_path / "results.json") == {"a": 1}
