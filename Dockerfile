FROM python:3.11-slim

WORKDIR /kb

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/    ./app/
COPY static/ ./static/

# Persistent storage for DB and uploaded PDFs
VOLUME ["/kb/data", "/kb/uploads"]

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
