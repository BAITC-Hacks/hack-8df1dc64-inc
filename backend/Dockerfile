FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY data/requirements.txt /app/data/requirements.txt
RUN pip install --no-cache-dir -r /app/data/requirements.txt
COPY data /app/data
COPY backend /app/backend
RUN useradd --uid 10001 --create-home wind && mkdir -p /app/backend/runtime && chown -R wind:wind /app/backend/runtime
USER wind
EXPOSE 8000
CMD ["python", "-m", "backend.server", "--host", "0.0.0.0"]
