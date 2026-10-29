FROM registry.chargeops.lab:5000/chargeops/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY ai-gateway/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -m pip uninstall -y pip setuptools wheel

COPY ai-gateway/ ./ai_gateway/
COPY common/ ./common/

RUN useradd --uid 10001 --create-home --no-log-init app
USER 10001:10001

# Check the final runtime after removing build tools, as the actual service user.
RUN python -m ai_gateway.runtime_check

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "ai_gateway.health:app", "--host", "0.0.0.0", "--port", "8000"]
