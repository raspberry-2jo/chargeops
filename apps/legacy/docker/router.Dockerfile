FROM registry.chargeops.lab:5000/chargeops/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /workspace
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home app
COPY app/ ./app/
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.router:app", "--host", "0.0.0.0", "--port", "8080"]