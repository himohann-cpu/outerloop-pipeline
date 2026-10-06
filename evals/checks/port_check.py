"""Verify check: the port the container listens on matches the port the deploy
script publishes and health-checks."""
import re
import sys
from pathlib import Path

dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
deploy = Path("scripts/deploy.sh").read_text(encoding="utf-8")

listen = re.search(r'--port"?,?\s*"?(\d+)', dockerfile)
publish = re.search(r"-p\s+(\d+):(\d+)", deploy)
health = re.search(r"curl[^\n]*localhost:(\d+)", deploy)
if not (listen and publish and health):
    print("Could not find the listen port, the -p mapping or the health-check URL.")
    sys.exit(1)

host_port, container_port = publish.group(1), publish.group(2)
problems = []
if listen.group(1) != container_port:
    problems.append(f"app listens on {listen.group(1)} but deploy.sh publishes container port {container_port}")
if health.group(1) != host_port:
    problems.append(f"health check hits {health.group(1)} but the published host port is {host_port}")
if problems:
    print("; ".join(problems))
    sys.exit(1)
print("Listen, publish and health-check ports agree.")
