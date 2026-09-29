FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY tower_bot ./tower_bot
# uid 99 has no user in python:3.12-slim yet (verified), so this must succeed;
# no `|| true` here so a real useradd failure fails the build instead of
# silently shipping a root container.
RUN pip install --no-cache-dir . && useradd -u 99 -g 100 -M -s /usr/sbin/nologin bot
USER 99:100
CMD ["python", "-m", "tower_bot"]
