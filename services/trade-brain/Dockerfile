FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev

COPY src ./src
COPY start-railway.sh ./start-railway.sh
RUN chmod +x ./start-railway.sh

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["./start-railway.sh"]
