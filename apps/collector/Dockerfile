FROM registry.chargeops.lab:5000/chargeops/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY collector/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -m pip uninstall -y pip setuptools wheel

COPY collector/collector_v1.py ./collector_v1.py

COPY common/runtime_security.py ./runtime_security.py
RUN useradd --uid 10001 --create-home --no-log-init app
RUN python -m runtime_security --harden
USER 10001:10001
RUN python -m runtime_security chargeops-collector

ENTRYPOINT ["python", "collector_v1.py"]
CMD ["check"]
