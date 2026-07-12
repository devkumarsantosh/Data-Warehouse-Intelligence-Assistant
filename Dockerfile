FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY config ./config
COPY pyproject.toml .

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "cdwia.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
