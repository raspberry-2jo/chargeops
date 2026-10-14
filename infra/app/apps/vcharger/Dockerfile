FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY vcharger/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY vcharger/vcharger.py ./vcharger.py

RUN useradd --uid 10001 --create-home --no-log-init app
USER 10001:10001

EXPOSE 8081
ENTRYPOINT ["python", "vcharger.py"]
CMD ["--url", "ws://csms-v2:9000/ocpp", "--ids", "CH-01", "--ramp", "1", "--control-port", "8081"]
