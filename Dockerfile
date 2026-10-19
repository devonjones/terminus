# `terminus exporter`: passive Prometheus metrics for a Seestar. See deploy/exporter/.
FROM python:3.11-slim
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /src
USER nobody
EXPOSE 9877
ENTRYPOINT ["terminus", "exporter"]
