FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY collector/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY collector/collector_v1.py ./collector_v1.py

RUN useradd --uid 10001 --create-home --no-log-init app
USER 10001:10001

ENTRYPOINT ["python", "collector_v1.py"]
CMD ["check"]
