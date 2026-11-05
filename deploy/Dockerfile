FROM docker:28-cli AS docker_cli
FROM python:3.12-slim-bookworm
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /srv/docsrunner
COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
COPY sandbox ./sandbox
COPY proxy ./proxy
COPY demo_docs ./demo_docs
COPY docs/video ./docs/video
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "172.29.0.2"]

