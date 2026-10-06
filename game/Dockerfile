# Game sandbox image: the poker engine plus every toolchain/library a student
# bot may use. One container per match or build; see run_match.py for the
# isolation model and scrimmage/worker/sandbox.py for the container limits.
#
#   docker build -t scrimmage-game:latest game/
FROM python:3.13-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        default-jdk-headless \
        libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /opt/bot-environment
COPY pyproject.toml uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-cache --compile-bytecode \
    && rm /usr/local/bin/uv
ENV PATH=/opt/venv/bin:$PATH

COPY engine.py config_template.py run_match.py /opt/scrimmage/

WORKDIR /game
ENTRYPOINT ["python3", "/opt/scrimmage/run_match.py"]
