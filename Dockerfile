# Minimal container image for the demo app (app/main.py + app/calculator.py).
# Built by the "build" stage of ci-pipeline.yml and smoke-tested in "deploy".
FROM python:3.12-slim

WORKDIR /app

# Install only the app's own dependencies (not the CI/agent tooling in
# requirements.txt) so the image stays small.
COPY app/requirements.txt app/requirements.txt
COPY app/nonexistent.txt .
RUN pip install --no-cache-dir -r app/requirements.txt

COPY app/ app/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
