FROM python:3.13-alpine

RUN apk add --no-cache arp-scan

WORKDIR /app
COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY backend ./backend
COPY www ./www

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "backend.main"]
