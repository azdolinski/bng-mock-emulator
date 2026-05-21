FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dictionary /app/dictionary
COPY src /app/src

ENV BNG_DICTIONARY=/app/dictionary \
    BNG_API_HOST=0.0.0.0 \
    BNG_API_PORT=8080 \
    BNG_COA_HOST=0.0.0.0 \
    BNG_COA_PORT=3799 \
    BNG_COA_SECRET=testing123 \
    BNG_NAS_IP=10.255.0.1 \
    BNG_RADIUS_SECRET=testing123

EXPOSE 8080/tcp
EXPOSE 3799/udp

CMD ["python", "-m", "src.app"]
