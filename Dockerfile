FROM python:3.12-slim

WORKDIR /app

# Install FFmpeg and Noto fonts covering common scripts, including CJK.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-core \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install poetry==1.8.1

# Configure poetry to not use virtual environments inside container
RUN poetry config virtualenvs.create false

# Copy dependency definitions
COPY pyproject.toml ./
RUN poetry install --no-interaction --no-ansi --no-root

# Copy application code
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
