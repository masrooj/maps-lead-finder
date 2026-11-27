# Same deps as Vercel (requirements.txt). Playwright is optional local-only.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG BUILD_MAPS_VERIFY=0
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
  && if [ "$BUILD_MAPS_VERIFY" = "1" ]; then \
       pip install --no-cache-dir playwright==1.49.1 \
       && playwright install --with-deps chromium; \
     fi

COPY app ./app
COPY index.py ./index.py

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
