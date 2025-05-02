# Use a specific Python version slim base image
FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=nonedge \
    PORT=8000

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Download Edge driver and place it in backend folder
RUN mkdir -p /app/backend && \
    python -c "from webdriver_manager.microsoft import EdgeChromiumDriverManager; import shutil; driver_path = EdgeChromiumDriverManager().install(); shutil.copy(driver_path, '/app/backend/edgedriver')" && \
    chmod +x /app/backend/edgedriver

# Expose the port Railway expects
EXPOSE $PORT

# Command to run the application
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
