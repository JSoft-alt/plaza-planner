FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLANNER_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home planner
COPY . .
RUN mkdir -p /app/data && chown -R planner:planner /app
USER planner
EXPOSE 8011
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
