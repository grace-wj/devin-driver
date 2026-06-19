FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# entrypoint — rename when you build the orchestrator
CMD ["python", "main.py"]
