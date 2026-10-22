import html
import platform
import re
from datetime import datetime

import pytest
import pytest_html
from pytest_metadata.plugin import metadata_key

STAGE = re.compile(r"^stage (\d+) (\S+)\s+(PASS|FAIL)\s+(.*)$", re.MULTILINE)
LOG_LINES = 200
EXTRA_CHARS = 2_000_000
COLUMNS = [("repo", "Repo"), ("kind", "Type"), ("env", "Env"), ("variant", "Variant")]


def pytest_sessionstart(session):
    meta = session.config.stash[metadata_key]
    meta.clear()
    meta["Workspace"] = str(session.config.mesh.ws.root)
    meta["Host"] = platform.node()
    meta["Python"] = platform.python_version()


def pytest_html_report_title(report):
    report.title = f"mesh-test · {datetime.now():%Y-%m-%d %H:%M}"


def pytest_html_results_summary(prefix, summary, postfix, session):
    s = session.config.mesh
    changed = sorted(n for n, files in s.changes.items() if files)
    chips = "".join(f'<span class="mesh-chip">{html.escape(n)}</span>' for n in changed)
    scope = "every test (--all)" if s.run_all else f"changes since {s.since}"
    prefix.append(
        '<div class="mesh-run">'
        f'<div><b>Tier</b>{html.escape(s.tier)}</div>'
        f'<div><b>Scope</b>{html.escape(scope)}</div>'
        f'<div><b>Changed</b>{chips or "<span class=mesh-chip>none</span>"}</div>'
        f'<div><b>Output</b><code>{html.escape(str(s.out))}</code></div>'
        '</div>')


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when != "call" or not hasattr(item, "repo"):
        return report
    log = getattr(item, "log", None)
    text = log.read_text(errors="replace") if log and log.is_file() else ""
    report.mesh = {
        "repo": item.repo.path.name,
        "kind": item.test.get("type", "host"),
        "env": item.test.get("env", "local"),
        "variant": item.variant.label if item.variant else "",
        "firmware": getattr(item, "firmware_name", ""),
        "log": str(log) if log else "",
        "stages": STAGE.findall(text),
        "tail": "\n".join(text.splitlines()[-LOG_LINES:]),
        "results": getattr(item, "results", None) or {},
        "verdicts": getattr(item, "verdicts", None) or [],
    }
    extras = []
    if text:
        extras.append(pytest_html.extras.text(text[-EXTRA_CHARS:], name="output.log"))
    if report.mesh["results"]:
        extras.append(pytest_html.extras.json(report.mesh["results"], name="results.json"))
    report.extras = extras
    return report


def _metrics_table(results, verdicts):
    judged = {v["name"]: v for v in verdicts}
    names = list(judged) + [n for n in results if n not in judged]
    rows = []
    for name in names:
        v = judged.get(name)
        got = results.get(name)
        value = "not reported" if got is None else (f"{got:.4g}" if isinstance(got, float) else str(got))
        mark = "" if v is None else ("PASS" if v["ok"] else "FAIL")
        cls = "" if v is None else ("mesh-pass" if v["ok"] else "mesh-fail")
        rows.append(f'<tr class="{cls}"><td>{html.escape(name)}</td><td>{html.escape(value)}</td>'
                    f'<td>{html.escape(v["rule"]) if v else ""}</td>'
                    f'<td>{f"<span class=mesh-verdict>{mark}</span>" if mark else ""}</td></tr>')
    return ('<table class="mesh-stages"><tr><th>Metric</th><th>Value</th><th>Expected</th>'
            f'<th>Verdict</th></tr>{"".join(rows)}</table>')


def pytest_html_results_table_header(cells):
    cells[2:2] = [f'<th class="sortable" data-column-type="{key}">{label}</th>'
                  for key, label in COLUMNS]


def pytest_html_results_table_row(report, cells):
    m = getattr(report, "mesh", None) or {"repo": report.nodeid.split("/")[0]}
    name = report.nodeid.rsplit("::", 1)[-1]
    if report.when != "call":
        name += f" ({report.when})"
    kind = m.get("kind", "")
    variant = html.escape(m.get("variant", ""))
    if m.get("firmware"):
        variant += f'<div class="mesh-firmware">{html.escape(m["firmware"])}</div>'
    cells[1] = f'<td class="col-testId">{html.escape(name)}</td>'
    cells[2:2] = [
        f'<td class="col-repo">{html.escape(m.get("repo", ""))}</td>',
        f'<td class="col-kind"><span class="mesh-badge mesh-{kind}">{kind}</span></td>' if kind
        else '<td class="col-kind"></td>',
        f'<td class="col-env">{html.escape(m.get("env", ""))}</td>',
        f'<td class="col-variant">{variant}</td>',
    ]


def _reason(report):
    if report.skipped and isinstance(report.longrepr, tuple):
        return report.longrepr[2].removeprefix("Skipped: "), "mesh-skip"
    if report.failed:
        lines = (report.longreprtext or "failed").splitlines()
        return lines[0], "mesh-fail"
    return None, None


def pytest_html_results_table_html(report, data):
    m = getattr(report, "mesh", None)
    if m is None:
        return
    parts = []
    reason, cls = _reason(report)
    failed_stages = [s for s in m["stages"] if s[2] == "FAIL"]
    if report.failed and failed_stages:
        n, stage, _, found = failed_stages[0]
        reason = f"stage {n} ({stage}) failed: {found}"
    if reason:
        parts.append(f'<div class="mesh-reason {cls}">{html.escape(reason)}</div>')
    if m["stages"]:
        rows = "".join(
            f'<tr class="mesh-{verdict.lower()}"><td>{n}</td><td>{html.escape(stage)}</td>'
            f'<td><span class="mesh-verdict">{verdict}</span></td><td>{html.escape(found)}</td></tr>'
            for n, stage, verdict, found in m["stages"])
        parts.append('<table class="mesh-stages"><tr><th>#</th><th>Stage</th><th>Verdict</th>'
                     f'<th>What it found</th></tr>{rows}</table>')
    if m.get("results") or m.get("verdicts"):
        parts.append(_metrics_table(m.get("results") or {}, m.get("verdicts") or []))
    if m["tail"]:
        parts.append(f'<div class="mesh-logpath">{html.escape(m["log"])} · last {LOG_LINES} lines</div>'
                     f'<div class="mesh-log">{html.escape(m["tail"])}</div>')
    data[:] = ["".join(parts) or "No output."]
