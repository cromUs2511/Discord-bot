FROM python:3.12-slim

# Unbuffered output, non-interactive debian frontend, and Asia/Manila timezone
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Manila

WORKDIR /app

# Install base OS dependencies for Playwright and timezone data
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser and its Linux OS system dependencies
RUN playwright install --with-deps chromium

# Copy application source code
COPY . .

# Run the Discord bot
CMD ["python", "main.py"]
