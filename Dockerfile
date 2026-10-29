FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE brief.example.md brief.example-finance.md ./
COPY db db
COPY gate gate
COPY unsubscribe unsubscribe
COPY inbound inbound
COPY prospecting prospecting
COPY agent agent
RUN pip install --no-cache-dir ".[ses,claude,mailtester]"

RUN useradd --system --uid 10001 gate && mkdir /data /config && chown gate /data
USER gate
ENV GATE_DB_PATH=/data/gate.db \
    GATE_OUTBOX_DIR=/data/outbox \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["gate"]
CMD ["status"]
