FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tracker_manager.py .

ENV GUARDIAN_DB=/data/guardian.db

VOLUME /data

ENTRYPOINT ["python", "tracker_manager.py"]
