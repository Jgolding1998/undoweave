"""Stdlib recording development benchmark; only generated synthetic projects.

Run from a normal clone with undoweave==0.4.0 installed in this interpreter.
No source import override, model call, network request, project execution or
automatic filesystem cleanup is used. Raw results stay under ignored work/.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import stat
import subprocess
import sys
import threading
import time


HERE = Path(__file__).absolute().parent
CHECKOUT = HERE.parent.parent
WORKER = HERE / "worker.py"
READ_LIMIT = 1024 * 1024


class Refusal(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def checked_chain(path):
    for candidate in [*reversed(path.parents), path]:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if linked(info):
            raise Refusal("linked_output_or_checkout_path")
        if candidate != path and not stat.S_ISDIR(info.st_mode):
            raise Refusal("non_directory_parent")


def create_output(value):
    supplied = Path(value).expanduser()
    if ".." in supplied.parts:
        raise Refusal("parent_traversal_in_output")
    output = supplied.absolute()
    work = CHECKOUT / "work"
    checked_chain(HERE)
    checked_chain(output)
    if work not in output.parents:
        raise Refusal("output_must_be_a_new_directory_under_checkout_work")
    for component in output.relative_to(work).parts:
        if (component.rstrip(" .") != component or any(ord(char) < 32 for char in component)
                or any(char in component for char in ':<>"|?*')
                or re.match(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9]|LPT[0-9])(?:\.|$)", component, re.I)):
            raise Refusal("unsafe_output_component")
    ignore = CHECKOUT / ".gitignore"
    if not ignore.is_file() or "work/" not in ignore.read_text(encoding="utf-8").splitlines():
        raise Refusal("checkout_work_ignore_rule_missing")
    # Refuse even an empty existing directory. mkdir is the final exclusive
    # ownership check; existing data is never deleted, cleared or reused.
    if os.path.lexists(output):
        raise Refusal("output_already_exists")
    missing = []
    parent = output.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    if not parent.is_dir():
        raise Refusal("output_parent_not_directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    checked_chain(output)
    output.mkdir(mode=0o700)
    if os.name != "nt" and (output.stat().st_mode & 0o077):
        raise Refusal("output_not_owner_private")
    return output


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")


def fixture_path(path):
    parsed = PurePosixPath(path)
    if (not isinstance(path, str) or not path.isascii() or parsed.is_absolute()
            or "\\" in path or ":" in path or len(path) > 100
            or any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/"))
            or parsed.suffix not in {".txt", ".json"}):
        raise Refusal("unsafe_fixture_path")
    return path


def load_benchmark():
    manifest = json.loads((HERE / "manifest.json").read_bytes())
    expected_names = {"README.md", "run.py", "worker.py", "cases.json", "expected-transitions.json"}
    if set(manifest["files"]) != expected_names:
        raise Refusal("unexpected_manifest_files")
    for name, expected in manifest["files"].items():
        data = (HERE / name).read_bytes()
        if len(data) != expected["bytes"] or digest(data) != expected["sha256"]:
            raise Refusal("benchmark_manifest_mismatch")
    producer = json.loads((HERE / "cases.json").read_bytes())
    expected = json.loads((HERE / "expected-transitions.json").read_bytes())
    if len(producer["cases"]) != 20 or len(expected["cases"]) != 20:
        raise Refusal("unexpected_case_count")
    ids = set()
    for case in producer["cases"]:
        if case["case_id"] in ids or not re.fullmatch(r"case-[0-9a-f]{12}", case["case_id"]):
            raise Refusal("invalid_or_duplicate_case_id")
        ids.add(case["case_id"])
        for files in (case["initial_files"], case["final_files"]):
            if len(files) > 3:
                raise Refusal("fixture_file_count")
            for path, specification in files.items():
                fixture_path(path)
                data = specification["text"].encode("utf-8")
                if specification["encoding"] != "utf-8" or len(data) > 1024 or digest(data) != specification["sha256"]:
                    raise Refusal("fixture_bytes_mismatch")
        if len(case["operations"]) > 20:
            raise Refusal("fixture_operation_count")
        for operation in case["operations"]:
            for mutation in operation.get("mutations", [operation]):
                if "path" in mutation:
                    fixture_path(mutation["path"])
                if "text" in mutation and len(mutation["text"].encode("utf-8")) > 1024:
                    raise Refusal("fixture_mutation_size")
    if ids != {case["case_id"] for case in expected["cases"]}:
        raise Refusal("expected_case_ids_mismatch")
    transitions = [item for case in expected["cases"] for item in case["transitions"]]
    if len(transitions) != 29 or Counter(item["status"] for item in transitions) != {"observed": 27, "applied": 2}:
        raise Refusal("expected_transition_counts_mismatch")
    return producer, expected, manifest


def child_options():
    options = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options.update(startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    return options


def child_command(request):
    # -I excludes cwd, PYTHONPATH and user site packages. The worker imports
    # the installed distribution; no checkout/src insertion is permitted.
    return [sys.executable, "-I", "-u", str(WORKER), json.dumps(request)]


def one_shot(request, output, name):
    result = subprocess.run(child_command(request), cwd=output, capture_output=True,
                            text=True, encoding="utf-8", timeout=15, **child_options())
    save(output / "raw" / (name + ".json"), {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode != 0 or len(result.stdout.encode()) > READ_LIMIT:
        raise Refusal("isolated_worker_failed")
    response = json.loads(result.stdout)
    if "driver_error" in response:
        raise Refusal("isolated_worker_error")
    return response


class Child:
    def __init__(self, root, state, scopes, output, name, package):
        self.output, self.name = output, name
        self.raw_stdout, self.raw_stderr = [], []
        self.responses = queue.Queue(maxsize=8)
        self.process = subprocess.Popen(child_command({"mode": "record", "root": str(root), "state": str(state), "scopes": scopes}),
            cwd=output, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", **child_options())

        def stdout_reader():
            try:
                while True:
                    line = self.process.stdout.readline(READ_LIMIT + 1)
                    if not line:
                        self.responses.put(None, timeout=1)
                        return
                    if len(line) > READ_LIMIT:
                        self.responses.put({"driver_error": "response_limit"}, timeout=1)
                        return
                    self.raw_stdout.append(line)
                    self.responses.put(json.loads(line), timeout=1)
            except (Exception,):
                try:
                    self.responses.put({"driver_error": "response_read_failed"}, timeout=1)
                except queue.Full:
                    pass

        def stderr_reader():
            # Bound diagnostic retention while continuing to drain the pipe.
            retained = 0
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    return
                if retained < 32768:
                    self.raw_stderr.append(chunk[:32768 - retained])
                    retained += len(chunk[:32768 - retained])

        self.readers = [threading.Thread(target=stdout_reader, daemon=True), threading.Thread(target=stderr_reader, daemon=True)]
        for reader in self.readers:
            reader.start()
        try:
            self.ready = self.read()
            if not self.ready.get("ready") or self.ready.get("package") != package:
                raise Refusal("worker_package_changed")
        except BaseException:
            self.dispose()
            raise

    def read(self):
        response = self.responses.get(timeout=12)
        if response is None or "driver_error" in response:
            raise Refusal("owned_worker_response_failed")
        return response

    def call(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()
        return self.read()

    def close(self):
        response = self.call({"kind": "close"})
        self.process.stdin.close()
        code = self.process.wait(timeout=5)
        if code != 0 or response["status"]["state"] != "stopped":
            raise Refusal("unclean_worker_shutdown")
        self.dispose()
        return response

    def dispose(self):
        # Only this Popen-owned child is ever terminated; no process search,
        # shell command, taskkill tree, or filesystem deletion is used.
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        for reader in self.readers:
            reader.join(timeout=2)
        for handle in (self.process.stdin, self.process.stdout, self.process.stderr):
            if handle is not None and not handle.closed:
                handle.close()
        if not getattr(self, "saved", False):
            save(self.output / "raw" / (self.name + ".json"), {
                "stdout": self.raw_stdout, "stderr": self.raw_stderr, "exit_code": self.process.returncode})
            self.saved = True


def mutate(root, operation):
    target = root / fixture_path(operation["path"])
    checked_chain(target)
    if operation["kind"] == "outside_write":
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(operation["text"].encode("utf-8"))
    elif operation["kind"] == "outside_delete":
        target.unlink()
    else:
        raise Refusal("invalid_fixture_mutation")


def materialized(root):
    result = {}
    for target in root.rglob("*"):
        if target.is_file() and target.name != ".undoweave-id":
            data = target.read_bytes()
            result[target.relative_to(root).as_posix()] = {"encoding": "utf-8", "text": data.decode("utf-8"), "sha256": digest(data)}
    return result


def run_case(case, output, package):
    base = output / "cases" / case["case_id"]
    root, state = base / "project", base / "state"
    root.mkdir(mode=0o700, parents=True)
    for path, specification in case["initial_files"].items():
        target = root / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(specification["text"].encode("utf-8"))
    paths = set(case["initial_files"]) | set(case["final_files"])
    for operation in case["operations"]:
        paths.update(item["path"] for item in operation.get("mutations", [operation]) if "path" in item)
    scopes = sorted({path.split("/")[0] for path in paths})
    child, session_count, trace = None, 0, []
    try:
        for operation in case["operations"]:
            kind = operation["kind"]
            receipt = {"kind": kind, "operation_id": operation["operation_id"]}
            if kind == "start_recorder":
                if child is not None:
                    raise Refusal("overlapping_fixture_sessions")
                session_count += 1
                child = Child(root, state, scopes, output, case["case_id"] + "-session-" + str(session_count), package)
            elif kind == "stop_recorder":
                if child is None:
                    raise Refusal("missing_fixture_session")
                receipt["response"] = child.close()
                child = None
            elif kind == "observe_stable":
                receipt["checkpoint"] = operation["checkpoint"]
                receipt["response"] = child.call({"kind": "observe"})
            elif kind in {"outside_write", "outside_delete"}:
                mutate(root, operation)
            elif kind == "tool_write":
                receipt["response"] = child.call({"kind": "tool_write", "path": operation["path"], "text": operation["text"], "actor": operation["self_reported_actor_label"]})
            elif kind == "outside_mutation_group":
                started = time.monotonic()
                for mutation in operation["mutations"]:
                    mutate(root, mutation)
                duration = time.monotonic() - started
                receipt.update(duration_seconds=duration, inside_interval=duration < .25)
                if duration >= .25:
                    raise Refusal("mutation_group_boundary_inconclusive")
            else:
                raise Refusal("unknown_fixture_operation")
            trace.append(receipt)
        if child is None:
            raise Refusal("fixture_ended_without_active_session")
        child.close()
        child = None
        before_inspect = materialized(root)
        if before_inspect != case["final_files"]:
            raise Refusal("final_fixture_bytes_mismatch")
        export = one_shot({"mode": "export", "root": str(root), "state": str(state)}, output, case["case_id"] + "-fresh-export")
        if export["package"] != package or materialized(root) != before_inspect:
            raise Refusal("inspection_or_installed_package_changed")
        save(base / "producer-receipt.json", {"trace": trace, "sessions": session_count, "final_bytes_exact": True})
        save(base / "fresh-evidence.json", export)
        return export
    finally:
        if child is not None:
            child.dispose()


def signature(changes):
    windows, result = {}, []
    for change in changes:
        sample = change.get("observation")
        group = None
        if sample:
            key = sample["observed_from"], sample["observed_to"]
            group = windows.setdefault(key, len(windows))
        result.append([change["path"], change["before_hash"], change["after_hash"], change["status"], group])
    return result


def grade(case, expected, export):
    changes = list(reversed(export["changes"]))
    actual = [(item["path"], item["before_hash"], item["after_hash"], item["status"]) for item in changes]
    wanted = [(item["path"], item["before_hash"], item["after_hash"], item["status"]) for item in expected["transitions"]]
    per_path = lambda items: {path: [item for item in items if item[0] == path] for path, *_ in items}
    inspections = export["inspections"]
    observed = [item for item in changes if item["status"] == "observed"]
    tool = [item for item in changes if item["status"] == "applied"]
    checks = {
        "expected_transitions": Counter(actual) == Counter(wanted),
        "per_file_order": per_path(actual) == per_path(wanted),
        "unknown_outside_authors": all(item["actor"] == "unknown" and item["attribution"] == "unknown" for item in observed),
        "self_reported_tool_authors": all(item["attribution"] == "self_reported" for item in tool),
        "observed_not_undoable": all(not inspections[item["id"]]["undo_supported"] for item in observed),
        "no_invented_checks_or_cause": all(item["check_evidence"] == "not_captured" and item["cause"] == "not_inferred" for item in inspections.values()),
        "complete_small_diff_display": all(not item["diff_truncated"] for item in inspections.values()),
        "stopped_not_fake_liveness": export["status"]["observation"]["state"] == "stopped" and export["status"]["observation"]["liveness"] == "not_checked",
    }
    return {"case_id": case["case_id"], "pair_id": case["pair_id"], "checks": checks,
            "expected": len(wanted), "retrieved": len(actual), "observed": len(observed), "tool_recorded": len(tool),
            "missing": sum((Counter(wanted) - Counter(actual)).values()),
            "unexpected": sum((Counter(actual) - Counter(wanted)).values()),
            "signature": signature(changes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new private directory under this clone's ignored work/ tree; existing targets are refused")
    args = parser.parse_args()
    producer, expected, manifest = load_benchmark()
    manifest_digest = digest((HERE / "manifest.json").read_bytes())
    output = create_output(args.output)
    results, failures, package = [], [], None
    try:
        package = one_shot({"mode": "probe"}, output, "installed-package-probe")["package"]
        if not package["isolated_python"]:
            raise Refusal("worker_not_isolated")
        lookup = {case["case_id"]: case for case in expected["cases"]}
        for index, case in enumerate(producer["cases"], 1):
            try:
                export = run_case(case, output, package)
                results.append(grade(case, lookup[case["case_id"]], export))
            except Exception as error:
                failures.append({"case_id": case["case_id"], "type": type(error).__name__,
                                 "code": str(error) if isinstance(error, Refusal) else "case_operation_failed"})
                save(output / "failures" / (case["case_id"] + ".json"), {"type": type(error).__name__, "message": str(error)})
            print("recording regression: " + str(index) + "/20 completed", file=sys.stderr, flush=True)
        final_probe = one_shot({"mode": "probe"}, output, "final-installed-package-probe")["package"]
        if final_probe != package:
            raise Refusal("installed_package_changed_during_run")
        load_benchmark()
        if digest((HERE / "manifest.json").read_bytes()) != manifest_digest:
            raise Refusal("benchmark_changed_during_run")
    except Exception as error:
        failures.append({"type": type(error).__name__, "code": str(error) if isinstance(error, Refusal) else "run_operation_failed"})
        save(output / "run-failure.json", {"type": type(error).__name__, "message": str(error)})
    result_by_id = {item["case_id"]: item for item in results}
    informative = controls = 0
    pair_checks = []
    for pair in expected["pairs"]:
        ids = pair["case_ids"]
        passed = False
        if all(identifier in result_by_id for identifier in ids):
            equal = result_by_id[ids[0]]["signature"] == result_by_id[ids[1]]["signature"]
            passed = equal == pair["observational_equivalence_control"]
            informative += passed and not pair["observational_equivalence_control"]
            controls += passed and pair["observational_equivalence_control"]
        pair_checks.append(passed)
    checks_pass = all(all(item["checks"].values()) for item in results)
    passed = len(results) == 20 and not failures and checks_pass and all(pair_checks)
    summary = {
        "benchmark": "recording-development-20-v1", "status": "passed" if passed else "failed_or_inconclusive",
        "provenance": "20-case development regression corpus originally authored before implementation inspection; now public fixtures, not an unseen external benchmark",
        "package": package, "python": sys.version.split()[0], "platform": sys.platform,
        "benchmark_manifest_sha256": manifest_digest,
        "producer_data_sha256": manifest["files"]["cases.json"]["sha256"],
        "expected_cases": 20, "replayed_cases": len(results), "expected_stable_transitions": 29,
        "retrieved_stable_transitions": sum(item["retrieved"] for item in results),
        "observed_unknown_author_transitions": sum(item["observed"] for item in results),
        "tool_recorded_self_reported_transitions": sum(item["tool_recorded"] for item in results),
        "missing_transitions": sum(item["missing"] for item in results), "unexpected_transitions": sum(item["unexpected"] for item in results),
        "informative_pairs_distinguished": informative, "indistinguishable_honesty_controls": controls,
        "all_structural_checks_pass": checks_pass, "failure_count": len(failures), "failures": failures,
        "model_calls": 0, "model_question_accuracy": None,
        "limits": ["Small synthetic stable-checkpoint regression only; no universal capture, accuracy or model-superiority claim.",
                   "History distinguishes captured net states, not every save, verified authors, exact action time, atomic global states, semantic renames or failure causes.",
                   "Source-only uncertainty is correct calibration. The100 authored questions are not graded by this mechanical runner.",
                   "Fast save/revert while stopped remains deliberately unobservable. No live polling capture-rate claim.",
                   "Raw local statuses, journals, paths and evidence stay in the chosen ignored private output; this summary omits local paths."]}
    save(output / "mechanical-case-results.json", results)
    save(output / "public-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as error:
        print(json.dumps({"status": "refused", "code": str(error)}), file=sys.stderr)
        raise SystemExit(2)
    except Exception as error:
        # Avoid leaking a local absolute path or raw status in terminal output.
        print(json.dumps({"status": "failed", "type": type(error).__name__, "code": "benchmark_setup_failed"}), file=sys.stderr)
        raise SystemExit(2)
