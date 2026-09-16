FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATUS_VISUALIZER_DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

RUN groupadd --gid 10001 status-visualizer \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin status-visualizer \
    && mkdir --parents /data \
    && chown status-visualizer:status-visualizer /data

COPY --chown=status-visualizer:status-visualizer app ./app
COPY --chown=status-visualizer:status-visualizer run.py ./run.py

USER status-visualizer

EXPOSE 8092
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8092/api/health', timeout=2).read()"]

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8092", "--data-dir", "/data"]
