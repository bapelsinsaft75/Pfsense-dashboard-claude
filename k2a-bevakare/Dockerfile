FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bevaka.py .

EXPOSE 8080
ENV TZ=Europe/Stockholm \
    PYTHONUNBUFFERED=1 \
    SKARMDUMP_MAPP=/data/skarmdumpar

CMD ["python", "bevaka.py"]
