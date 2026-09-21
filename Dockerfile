# ---- stage 1: build the React bundle ----
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: python API, serving that bundle ----
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
# The admin system runs the scrapers and llm.py in-process, so these modules
# are part of the server, not just laptop tooling. .dockerignore keeps their
# data directories out - only the code is wanted here.
COPY questions_processing/ questions_processing/
COPY --from=web /app/frontend/dist frontend/dist
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
