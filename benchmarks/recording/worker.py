"""Owned isolated child for synthetic recording regression cases.

Import only an installed package. Never execute source from a fixture project.
Raw responses are private local benchmark artifacts, not publication material.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import time


def emit(payload):
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)


def main():
    import undoweave
    from undoweave.engine import Engine
    from undoweave.recorder import Recorder

    if version("undoweave") != "0.4.0" or undoweave.__version__ != "0.4.0":
        raise ValueError("installed_version_must_be_0_3_0")
    package = Path(undoweave.__file__).parent
    package_receipt = {
        "version": version("undoweave"),
        "isolated_python": bool(sys.flags.isolated),
        "source_sha256": {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                          for name in ("__init__.py", "engine.py", "recorder.py")},
    }
    request = json.loads(sys.argv[1])
    if request["mode"] == "probe":
        emit({"package": package_receipt})
        return
    engine = Engine(request["root"], request["state"])
    if request["mode"] == "export":
        changes = engine.history(200)
        emit({"package": package_receipt, "status": engine.status(), "changes": changes,
              "inspections": {item["id"]: engine.inspect_change(item["id"]) for item in changes}})
        return
    recorder = Recorder(engine, request["scopes"], interval=.25)
    try:
        recorder.start()
        emit({"ready": True, "package": package_receipt, "status": recorder.status()})
        for line in sys.stdin:
            command = json.loads(line)
            if command["kind"] == "close":
                emit({"closed": True, "status": recorder.stop()})
                return
            if command["kind"] == "observe":
                previous = recorder.status()["last_scan"]
                completed, deadline = 0, time.monotonic() + 8
                while completed < 2:
                    status = recorder.status()
                    if status["state"] in {"error", "stopping", "stopped"}:
                        raise RuntimeError("recorder_not_active")
                    if status["last_scan"] != previous:
                        completed += 1
                        previous = status["last_scan"]
                    if time.monotonic() >= deadline:
                        raise TimeoutError("stable_checkpoint_timeout")
                    time.sleep(.02)
                emit({"completed_scans": completed, "status": recorder.status(),
                      "changes": engine.history(200)})
            elif command["kind"] == "tool_write":
                current = engine.read(command["path"])
                emit({"change": engine.edit(command["path"], command["text"], command["actor"], current["sha256"])})
            else:
                raise ValueError("unknown_driver_command")
    finally:
        recorder.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # A parent stores this only in its explicitly chosen private output.
        emit({"driver_error": type(error).__name__, "code": getattr(error, "code", None)})
        raise SystemExit(1)
