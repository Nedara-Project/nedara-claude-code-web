# Nedara Claude Code Web — container image (Docker or Podman).
#
# The claude CLI comes from the claude-agent-sdk wheel. Claude runs its tools
# (git, shell, Python...) inside this container: extend the image with what
# your projects need.
FROM python:3.13-slim

ARG UID=1000
ARG GID=1000

RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ripgrep ca-certificates curl procps less \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${GID}" app \
    && useradd --uid "${UID}" --gid "${GID}" --create-home --shell /bin/bash app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
RUN rm -rf .venv node_modules data config.ini

USER app
ENV PYTHONUNBUFFERED=1 \
    NCCW_HOST=0.0.0.0 \
    NCCW_PORT=5070

EXPOSE 5070
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5070/login', timeout=4)"

ENTRYPOINT ["python", "app.py"]
