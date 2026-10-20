import os
import shutil
import signal
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from mesh_test import select
from mesh_test.changes import changed_files
from mesh_test.workspace import Workspace

HERE = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 1800
TAIL_LINES = 40


class MeshFailure(Exception):
    pass


def _split(value):
    return [v for v in (value or "").split(",") if v]


class State:
    def __init__(self, config):
        local_path = HERE / "local.yaml"
        local = (yaml.safe_load(local_path.read_text()) or {}) if local_path.is_file() else {}
        opt = config.getoption
        self.ws = Workspace(os.environ.get("MESH_WORKSPACE") or HERE.parent)
        self.tier = opt("mesh_tier")
        self.run_all = opt("mesh_all")
        self.since = opt("mesh_since")
        hardware = opt("mesh_hardware")
        self.hardware = set(_split(hardware)) if hardware is not None else set(local.get("hardware") or [])
        configs = opt("mesh_configs")
        configs = _split(configs) if configs is not None else list(local.get("default_configs") or [])
        self.configs = set() if "all" in configs else set(configs)
        self.firmware = dict(local.get("firmware") or {})
        for pair in opt("mesh_firmware"):
            key, _, value = pair.partition("=")
            self.firmware[key] = value
        out = opt("mesh_out")
        self.out = Path(out) if out else HERE / "out" / datetime.now().strftime("%Y%m%d-%H%M%S")
        self._changes = None

    @property
    def changes(self):
        if self._changes is None:
            self._changes = {name: changed_files(repo.path, self.since)
                             for name, repo in self.ws.repos.items()}
        return self._changes


def pytest_addoption(parser):
    group = parser.getgroup("mesh-test")
    group.addoption("--tier", dest="mesh_tier", choices=["smoke", "nightly"], default="smoke")
    group.addoption("--all", dest="mesh_all", action="store_true")
    group.addoption("--since", dest="mesh_since", default="origin/main")
    group.addoption("--hardware", dest="mesh_hardware", default=None)
    group.addoption("--configs", dest="mesh_configs", default=None)
    group.addoption("--firmware", dest="mesh_firmware", action="append", default=[])
    group.addoption("--out", dest="mesh_out", default=None)


def pytest_configure(config):
    config.mesh = State(config)
    for name in ("host", "sim", "hil"):
        config.addinivalue_line("markers", name)


def pytest_report_header(config):
    s = config.mesh
    changed = sorted(name for name, files in s.changes.items() if files)
    scope = "every test (--all)" if s.run_all else f"changes since {s.since}"
    return [f"mesh-test: tier {s.tier}, {scope}",
            f"changed repos: {', '.join(changed) or 'none'}",
            f"output: {s.out}"]


def pytest_collect_file(file_path, parent):
    if file_path.name == "tests.yaml" and parent.config.mesh.ws.repo_for(file_path.parent):
        return TestsFile.from_parent(parent, path=file_path)


def pytest_collection_modifyitems(config, items):
    s = config.mesh
    keep, drop = [], []
    for item in items:
        if isinstance(item, MeshItem):
            wanted = (select.in_tier(item.test, s.tier)
                      and select.in_configs(item.variant, s.configs)
                      and (s.run_all or select.is_affected(s.ws, item.repo, item.test, item.variant, s.changes)))
        else:
            wanted = True
        (keep if wanted else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


class TestsFile(pytest.File):
    def collect(self):
        ws = self.config.mesh.ws
        repo = ws.repo_for(self.path.parent)
        for test in ws.tests_file(repo.name).get("tests") or []:
            for variant in ws.variants_for(repo, test):
                name = test["id"] if variant is None else f"{test['id']}[{variant.label}]"
                yield MeshItem.from_parent(self, name=name, repo=repo, test=test, variant=variant)


class MeshItem(pytest.Item):
    def __init__(self, *, repo, test, variant, **kwargs):
        super().__init__(**kwargs)
        self.repo, self.test, self.variant = repo, test, variant
        self.add_marker(test.get("type", "host"))
        for tag in test.get("hardware") or []:
            self.add_marker(tag)

    def _command(self):
        env_name = self.test.get("env", "local")
        run = self.test["run"]
        if env_name == "local":
            return ["bash", "-c", run], self.test.get("tools") or []
        if env_name == "spangap":
            return ["spangap", "docker", "bash", "-c", run], ["spangap", "docker"]
        if env_name == "sim-mesh":
            if self.repo.name != "sim-mesh/sim-mesh":
                raise MeshFailure("env sim-mesh runs sim-mesh's own tests only")
            return [str(self.repo.path / "sim"), "test", self.test["id"]], ["docker"]
        raise MeshFailure(f"unknown env '{env_name}'")

    def runtest(self):
        s = self.config.mesh
        if self.test.get("type") == "hil":
            missing = set(self.test.get("hardware") or []) - s.hardware
            if missing or not s.hardware:
                pytest.skip(f"no bench hardware: {', '.join(sorted(missing)) or 'none given'}")
        cmd, tools = self._command()
        missing = [t for t in tools if not shutil.which(t)]
        if missing:
            pytest.skip(f"missing tools: {' '.join(missing)}")
        env = dict(os.environ, MESH_WORKSPACE=str(s.ws.root))
        if self.variant:
            firmware = (s.firmware.get(self.variant.label)
                        or s.firmware.get(f"{self.variant.owner}:{self.variant.name}"))
            if not firmware:
                pytest.skip(f"no firmware for {self.variant.label}: --firmware {self.variant.label}=NAME")
            env["TEST_FIRMWARE"] = firmware
        out = s.out / self.repo.path.name / self.name
        out.mkdir(parents=True, exist_ok=True)
        env["TEST_OUT"] = str(out)
        log = out / "output.log"
        timeout = self.test.get("timeout", DEFAULT_TIMEOUT)
        with open(log, "wb") as fh:
            proc = subprocess.Popen(cmd, cwd=self.repo.path, env=env, stdin=subprocess.DEVNULL,
                                    stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise MeshFailure(f"timed out after {timeout} s; output: {log}\n{_tail(log)}")
        if code:
            raise MeshFailure(f"exit {code}; output: {log}\n{_tail(log)}")

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, MeshFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, None, f"{self.repo.name}:{self.name}"


def _tail(path):
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-TAIL_LINES:])
