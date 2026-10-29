FROM registry.chargeops.lab:5000/chargeops/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY notifier/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -m pip uninstall -y pip setuptools wheel

COPY notifier/ ./notifier/
COPY common/ ./common/

COPY common/runtime_security.py ./runtime_security.py
RUN useradd --uid 10001 --create-home --no-log-init app
RUN python -m runtime_security --harden
USER 10001:10001
RUN python -m runtime_security chargeops-notifier

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "notifier.app:app", "--host", "0.0.0.0", "--port", "8000"]
