FROM registry.chargeops.lab:5000/chargeops/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY csms/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY csms/ ./csms/

RUN useradd --uid 10001 --create-home --no-log-init app
USER 10001:10001

EXPOSE 8000 9000 9100
CMD ["python", "-m", "uvicorn", "csms.app:app", "--host", "0.0.0.0", "--port", "8000"]
