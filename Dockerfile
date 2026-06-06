FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY pyproject.toml ./
RUN /root/.local/bin/uv sync --no-cache
COPY . ./
CMD ["/root/.local/bin/uv", "run", "python", "-m", "hermes_trading.run"]
