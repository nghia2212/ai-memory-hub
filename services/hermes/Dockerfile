FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /data && chown 10001:10001 /data
COPY upstream.json /app/upstream.json
COPY app /app/app
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8083 SERVICE_NAME=hermes HERMES_ENABLED=false
EXPOSE 8083
CMD ["python", "-m", "app.server"]
