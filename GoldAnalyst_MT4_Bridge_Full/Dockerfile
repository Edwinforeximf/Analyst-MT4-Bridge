FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ai_engine.py visuals.py export_docx.py market_feed.py mt4_feed.py live_engine.py ./
COPY static/ ./static/
ENV PYTHONUNBUFFERED=1
ENV STORAGE_DIR=/data
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
