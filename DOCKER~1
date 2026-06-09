FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema para psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Porta exposta pelo Railway via variável $PORT
ENV PORT=8000
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
