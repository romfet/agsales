FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Зависимости ставим от root (в system site-packages).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend ./backend

# Непривилегированный пользователь — контейнер НЕ должен работать от root (SAST).
# uvicorn слушает 5050 (>1024), права на /app отдаём appuser; рантайм в /app не пишет.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5050
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "5050"]
