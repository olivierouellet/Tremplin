FROM python:3.13-slim

WORKDIR /app

COPY cloud/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Cloud server code, templates, and locales
COPY cloud/cloud_server.py .
COPY cloud/templates/ templates/
COPY locales/ locales/

# Shared static assets from the Pi app (CSS, fonts, icons)
COPY static/css/  static/css/
COPY static/fonts/ static/fonts/
COPY static/icons/ static/icons/
COPY static/img/  static/img/

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["python", "cloud_server.py"]
