cat > tenant-app/Dockerfile <<'FILE_END'
FROM python:3.12-slim
RUN pip install --no-cache-dir flask gunicorn
WORKDIR /app
COPY app.py .
USER nobody
CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "1", "--worker-tmp-dir", "/tmp", "app:app"]
FILE_END
