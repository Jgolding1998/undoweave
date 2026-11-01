"""Install the built wheel into a new venv and test it away from checkout src/."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def main():
    repository = Path(__file__).resolve().parents[1]
    wheels = list((repository / "dist").glob("undoweave-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one wheel before this check.")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        venv.create(base / "env", with_pip=True)
        interpreter = base / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(interpreter), "-m", "pip", "install", "-c", str(repository / "constraints.txt"), str(wheels[0]) + "[mcp]"], check=True, env=environment, cwd=base, timeout=180)
        subprocess.run([str(interpreter), "-I", "-m", "undoweave", "demo"], check=True, env=environment, cwd=base, stdout=subprocess.DEVNULL, timeout=30)
        subprocess.run([str(interpreter), str(repository / "scripts" / "smoke_install.py")], check=True, env=environment, cwd=base, timeout=120)


if __name__ == "__main__":
    main()
