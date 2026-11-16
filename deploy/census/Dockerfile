FROM python:3.13-slim
RUN pip install --no-cache-dir gunicorn==26.2.0
WORKDIR /app
COPY backend/service.py backend/database.py backend/webapp.py backend/hosted.py ./
USER 10001:10001
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/census/healthz', timeout=3)"
CMD ["gunicorn", "hosted:create_app()", "--bind=0.0.0.0:8788", "--workers=1", "--threads=4", "--worker-connections=64", "--timeout=60", "--graceful-timeout=30", "--keep-alive=2", "--limit-request-line=2048", "--limit-request-fields=32", "--limit-request-field_size=4096"]
