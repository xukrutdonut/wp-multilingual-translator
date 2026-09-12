FROM python:3.11-slim

# Install SSH tools needed for remote synchronization
RUN apt-get update && apt-get install -y --no-install-recommends \
    sshpass \
    openssh-client \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY translator.py .
COPY glossary.json .

ENV PYTHONUNBUFFERED=1
ENV SSH_HOST=89.117.169.108
ENV SSH_PORT=65002
ENV SSH_USER=u691704582
ENV SSH_PASS="Mayo-1968!"
ENV WP_PATH=/home/u691704582/domains/neuropediatoolkit.org/public_html/
ENV DAILY_LIMIT_PER_LANG=1000
ENV BATCH_SIZE=100
ENV REQUEST_DELAY=0.25
ENV CYCLE_INTERVAL_HOURS=24
ENV LLM_API_URL=http://192.168.0.100:1235/v1
ENV LLM_MODEL="qwen2.5-coder-7b-instruct"

CMD ["python3", "-u", "translator.py"]
