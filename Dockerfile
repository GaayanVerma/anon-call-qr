FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py .

RUN useradd --create-home appuser && mkdir -p /app/instance && chown appuser /app/instance
USER appuser

# SQLite lives in /app/instance; mount a volume there to keep data across deploys.
ENV DATABASE_URL=sqlite:////app/instance/app.db
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "wsgi:app"]
