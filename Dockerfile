# The agent's API, as one always-on container.
#
# Deliberately NOT serverless. The demo is stateful on purpose -- prepare, then
# approve, then the interview exists, then the audit chain proves it happened in
# that order -- and a platform that discards the filesystem between requests
# breaks exactly the thing this is built to show. One small container with a
# disk is the right shape for it.

FROM python:3.12-slim

# Keeps the image small and the logs honest (no buffering, no .pyc clutter).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so editing source does not reinstall the world.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install fastapi "uvicorn[standard]" pydantic pyyaml

COPY hr_agent/ ./hr_agent/
COPY packs/ ./packs/
COPY deploy/start.sh ./deploy/start.sh
RUN chmod +x ./deploy/start.sh

# Where the SQLite database lives. Mount a disk here to keep state across
# restarts; without one the demo simply re-seeds on boot, which is fine.
ENV HR_VAR_DIR=/data
RUN mkdir -p /data

# Render and most platforms inject PORT. 4000 is the local default.
ENV PORT=4000
EXPOSE 4000

CMD ["./deploy/start.sh"]
