FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tracker_manager.py .

ENTRYPOINT ["python", "tracker_manager.py"]
