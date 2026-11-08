FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /var/lib/transport \
    && chown app:app /var/lib/transport
COPY --chown=app:app backend /app/backend
COPY --chown=app:app deploy/backup.py /app/backup.py
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--root-path", "/api", "--proxy-headers", "--forwarded-allow-ips", "*"]
