FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py extraction.py worker.py ./
CMD ["uvicorn","app:app","--host","0.0.0.0"]
