FROM python:3.12-slim

WORKDIR /app

# Копируем только requirements для кэширования слоя
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код
COPY app/ ./app/
COPY main.py .

# Не-root пользователь (хорошая практика, но опционально)
# RUN useradd -m appuser && chown -R appuser /app
# USER appuser

EXPOSE 8000

# Один воркер — критично при лимите 0.5 CPU
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]