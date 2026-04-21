# Dockerfile
# Use a pinned, stable Python runtime (Debian 12 Bookworm) to prevent package tree instability
FROM python:3.10-slim-bookworm

# Set environment variables for non-interactive installation and locale settings
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Install system dependencies required by Chromium in headless mode.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        libappindicator3-1 \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first to leverage Docker cache
COPY requirements.txt .

# FIX: Use '-r' to read from the file instead of '.' which looks for setup.py
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot application code into the container
COPY . .

# Define environment variable for BOT_TOKEN (Railway will inject this at runtime)
ENV BOT_TOKEN=${BOT_TOKEN}

# Run the background worker strictly
CMD ["python", "bot.py"]
