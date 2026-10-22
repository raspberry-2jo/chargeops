FROM registry.chargeops.lab:5000/chargeops/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY chatops-bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY chatops-bot/ ./chatops_bot/
COPY common/ ./common/

RUN useradd --uid 10001 --create-home --no-log-init app
USER 10001:10001

CMD ["python", "-m", "chatops_bot.app"]
