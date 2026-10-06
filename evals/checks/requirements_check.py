"""Verify check: app/requirements.txt still lists the required packages, and no
version specifier excludes the version that is actually installed.

    python evals/checks/requirements_check.py fastapi uvicorn
"""
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

required = {name.lower() for name in sys.argv[1:]}
problems, seen = [], set()
for raw in Path("app/requirements.txt").read_text(encoding="utf-8").splitlines():
    line = raw.split("#")[0].strip()
    if not line or line.startswith("-"):
        continue
    req = Requirement(line)
    seen.add(req.name.lower())
    try:
        installed = metadata.version(req.name)
    except metadata.PackageNotFoundError:
        continue
    if not req.specifier.contains(installed, prereleases=True):
        problems.append(f"{line!r} excludes the installed {req.name} {installed}")

for name in sorted(required - seen):
    problems.append(f"required dependency {name!r} was removed")

if problems:
    print("; ".join(problems))
    sys.exit(1)
print("app/requirements.txt is installable.")
