FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY ui/requirements.txt /tmp/ui-requirements.txt
RUN python -m pip install --no-cache-dir . -r /tmp/ui-requirements.txt

COPY config.toml ./
COPY data/ ./data/
COPY ui/ ./ui/

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/ui/jobs /app/ui/uploads /app/runs \
    && chown -R app:app /app/ui/jobs /app/ui/uploads /app/runs

USER app

EXPOSE 8501

CMD ["python", "-m", "streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--server.baseUrlPath=kuku"]
