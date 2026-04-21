# Dockerfile
# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables for non-interactive installation and locale settings
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Install system dependencies required by Chrome/Chromium in headless mode
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium-chromedriver \
        fonts-liberation \
        libappindicator3-1 \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir .

# Copy the bot application code into the container
COPY . .

# Define environment variable for BOT_TOKEN (Docker will look for this at runtime)
ENV BOT_TOKEN=${BOT_TOKEN}

# Expose port 80 if needed, but for Telegram bots using infinity_polling,
# we don't strictly need to open a port unless using webhooks.
# However, standard practice is CMD python bot.py

CMD ["python", "bot.py"]
