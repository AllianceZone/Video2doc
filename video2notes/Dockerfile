FROM python:3.11-slim

# ffmpeg is required by the pipeline (frame extraction, audio extraction/clips);
# libgl1/libglib2.0-0 are needed by opencv-python-headless at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Overridden by docker-compose for the worker service (celery -A celery_app worker)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
