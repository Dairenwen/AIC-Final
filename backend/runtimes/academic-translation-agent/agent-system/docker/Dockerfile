FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 tesseract-ocr tesseract-ocr-eng fonts-noto-cjk \
    && apt-get clean
COPY agent-core /app/agent-core
RUN pip install --no-cache-dir pdf2zh==1.9.11 tencentcloud-sdk-python-tmt==3.0.1478 \
    && pip install --no-cache-dir /app/agent-core
COPY agent-system/backend /app/backend
CMD ["uvicorn", "app:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]
