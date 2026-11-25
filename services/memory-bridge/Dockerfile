FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /data /data/backups && chown -R 10001:10001 /data
COPY services/memory-bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/orchestrator/__init__.py /app/services/orchestrator/__init__.py
COPY services/orchestrator/contracts.py /app/services/orchestrator/contracts.py
COPY services/memory-bridge/app /app/app
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
EXPOSE 8082
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082"]
