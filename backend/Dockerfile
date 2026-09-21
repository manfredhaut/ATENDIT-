FROM python:3.11-slim

# Variáveis de ambiente para otimização do Python no Docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /workspace

# Instalação de utilitários básicos do sistema operacional
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalação das dependências Python em camada intermediária com cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Cópia do código-fonte da aplicação
COPY . /workspace/

# Criação do diretório seguro para armazenamento do storage RAG
RUN mkdir -p /workspace/storage/tenants

EXPOSE 8000

# Healthcheck de liveness da API
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Comando padrão de inicialização
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
