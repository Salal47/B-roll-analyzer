FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV GRADIO_SERVER_NAME=0.0.0.0
EXPOSE 7860

CMD ["python", "app.py"]
