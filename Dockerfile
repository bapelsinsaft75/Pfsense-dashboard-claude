FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Stockholm \
    DATA_DIR=/data \
    WEB_PORT=8080 \
    NETFLOW_PORT=2055

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/dashboard
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

VOLUME ["/data"]
EXPOSE 8080/tcp 2055/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"WEB_PORT\",\"8080\")}/api/health',timeout=4)" || exit 1

CMD ["python", "-m", "app.main"]
