FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /data && chown 10001:10001 /data
COPY scaffold.py /app/scaffold.py
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8082
CMD ["python", "/app/scaffold.py"]
