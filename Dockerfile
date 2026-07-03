FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY engine ./engine
COPY llm ./llm
COPY npc ./npc
COPY gateway ./gateway
COPY gate ./gate
COPY world ./world
COPY scripts ./scripts

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000
CMD ["uvicorn", "engine.app:app", "--host", "0.0.0.0", "--port", "8000"]
