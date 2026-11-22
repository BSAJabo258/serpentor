FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ocrmypdf ffmpeg poppler-utils fonts-crimson fonts-inter fonts-dejavu-core tesseract-ocr tesseract-ocr-fra tesseract-ocr-spa tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-broadcast.txt ./
RUN pip install --no-cache-dir -r requirements-broadcast.txt
COPY bsi ./bsi
COPY tests ./tests
RUN useradd -m bsi && mkdir -p /data && chown bsi /data
USER bsi
ENV BSI_DATA_DIR=/data
EXPOSE 8080
CMD ["uvicorn", "bsi.api:app", "--host", "0.0.0.0", "--port", "8080"]
