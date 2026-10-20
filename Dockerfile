FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
VOLUME /app/data
EXPOSE 8080
CMD ["python", "-m", "bot"]
