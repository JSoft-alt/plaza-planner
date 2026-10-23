# OSRM and Python share one private host/container so new regions can be built
# automatically. Pin preprocessing and serving to the exact same engine image.
FROM ghcr.io/project-osrm/osrm-backend:26.10.0-debian@sha256:55dbfd47d984c2cacd64e32901b4321c86b5ec761438e69aa053dc78698a786f
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates passwd && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/planner-venv
ENV PATH="/opt/planner-venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLANNER_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home planner
COPY . .
RUN mkdir -p /app/data && chown -R planner:planner /app
USER planner
EXPOSE 8011
ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
