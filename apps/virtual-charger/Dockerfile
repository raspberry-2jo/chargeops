FROM registry.chargeops.lab:5000/chargeops/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY virtual-charger/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -m pip uninstall -y pip setuptools wheel

COPY virtual-charger/vcharger.py ./vcharger.py

COPY common/runtime_security.py ./runtime_security.py
RUN useradd --uid 10001 --create-home --no-log-init app
RUN python -m runtime_security --harden
USER 10001:10001
RUN python -m runtime_security chargeops-vcharger

EXPOSE 8081
ENTRYPOINT ["python", "vcharger.py"]
CMD ["--url", "ws://csms:9000/ocpp", "--ids", "CH-01", "--ramp", "1", "--control-port", "8081"]
