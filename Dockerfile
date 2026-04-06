# Use a slim Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (including those needed for some Python packages)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first (for better caching)
COPY requirements.txt .

# Install dependencies using the optimized requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port the app runs on (Railway's default is 5001 or 8080)
EXPOSE 5001

# Command to run the application, using Railway's PORT environment variable or defaulting to 5001
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-5001}"]
