import json
import operator
import re

RULE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(\S+)\s*$")
OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq,
       "!=": operator.ne, ">": operator.gt, "<": operator.lt}


class ExpectError(Exception):
    pass


def load(path):
    if not path.is_file():
        return None
    with open(path) as handle:
        return json.load(handle)


def judge(expect, results):
    verdicts = []
    for name, rule in (expect or {}).items():
        m = RULE.match(str(rule))
        if not m:
            raise ExpectError(f"expect {name}: {rule!r} is not like '>= 0.95'")
        op, want = m.groups()
        got = (results or {}).get(name)
        try:
            ok = got is not None and OPS[op](float(got), float(want))
        except (TypeError, ValueError):
            ok = False
        verdicts.append({"name": name, "rule": f"{op} {want}", "got": got, "ok": ok})
    return verdicts


def describe(verdict):
    if verdict["got"] is None:
        return f"{verdict['name']} not reported, expected {verdict['rule']}"
    return f"{verdict['name']} {verdict['got']}, expected {verdict['rule']}"
