FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy[asyncio]>=2.0.30" \
        "asyncpg>=0.29" "psycopg[binary]>=3.2" "alembic>=1.13" "pydantic[email]>=2.7" \
        "pydantic-settings>=2.3" "pyjwt>=2.8" "httpx>=0.27" "boto3>=1.34"

COPY app ./app
COPY db ./db
COPY migrations ./migrations
COPY alembic.ini ./

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
