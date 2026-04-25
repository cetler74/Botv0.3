FROM python:3.11-slim

WORKDIR /app

# Copy all Python files
COPY *.py ./

# List files to debug
RUN ls -la /app/

CMD ["python", "-c", "import os; print('Files in /app:'); print(os.listdir('/app'))"]