FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --no-cache-dir -r ./backend/requirements.txt

COPY risk-engine/ ./risk-engine/
RUN python -m pip install --no-cache-dir -e ./risk-engine

RUN groupadd --system --gid 10001 heatshield \
    && useradd --system --uid 10001 --gid heatshield --create-home heatshield

COPY --chown=10001:10001 backend/ ./backend/
COPY --chown=10001:10001 ai/ ./ai/
COPY --chown=10001:10001 data/demo/ ./data/demo/

WORKDIR /app/backend
USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
