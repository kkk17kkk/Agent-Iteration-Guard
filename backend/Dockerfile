FROM python:3.12-slim

WORKDIR /app
COPY backend/pyproject.toml ./
RUN python -c "import tomllib; project = tomllib.load(open('pyproject.toml', 'rb')); open('/tmp/requirements.txt', 'w').write(chr(10).join(project['project']['dependencies']))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend/agentguard ./agentguard
COPY examples ./examples
COPY frontend/public ./frontend/public
RUN pip install --no-cache-dir --no-deps .

ENV AGENTGUARD_DB=/data/agentguard.db
ENV AGENTGUARD_ASSET_ROOT=/app
ENV AGENTGUARD_UPLOAD_ROOT=/data/uploads
EXPOSE 8000
CMD ["/bin/sh", "-c", "uvicorn agentguard.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
