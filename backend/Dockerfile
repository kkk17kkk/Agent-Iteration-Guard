FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY agentguard ./agentguard
RUN pip install --no-cache-dir .

ENV AGENTGUARD_DB=/data/agentguard.db
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "agentguard.api:app", "--host", "0.0.0.0", "--port", "8000"]
