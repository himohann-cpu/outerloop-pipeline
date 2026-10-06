"""Verify check: every COPY/ADD source in the Dockerfile exists in the build context."""
import glob
import sys
from pathlib import Path

missing = []
for raw in Path("Dockerfile").read_text(encoding="utf-8").splitlines():
    parts = raw.split()
    if len(parts) < 3 or parts[0].upper() not in ("COPY", "ADD"):
        continue
    args = [p for p in parts[1:] if not p.startswith("--")]
    for source in args[:-1]:
        if source.startswith(("http://", "https://")):
            continue
        if not (Path(source).exists() or glob.glob(source)):
            missing.append(source)

if missing:
    print("Dockerfile copies paths that are not in the build context: " + ", ".join(missing))
    sys.exit(1)
print("Dockerfile COPY/ADD sources all exist.")
