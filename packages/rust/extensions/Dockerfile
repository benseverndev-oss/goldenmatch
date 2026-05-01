FROM postgres:16-bookworm AS builder

RUN apt-get update && apt-get install -y \
    build-essential \
    libclang-dev \
    pkg-config \
    postgresql-server-dev-16 \
    python3-dev \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install pgrx
RUN cargo install cargo-pgrx --version "0.12.9" --locked
RUN cargo pgrx init --pg16=/usr/lib/postgresql/16/bin/pg_config

# Install goldenmatch Python package
RUN pip3 install --break-system-packages goldenmatch>=1.1.0

# Build extension
WORKDIR /build
COPY . .
WORKDIR /build/postgres
RUN cargo pgrx install --pg-config=/usr/lib/postgresql/16/bin/pg_config --release
RUN cp sql/goldenmatch_pg--0.1.0.sql /usr/share/postgresql/16/extension/

# ── Final image ──
FROM postgres:16-bookworm

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages goldenmatch>=1.1.0

# Copy extension files from builder
COPY --from=builder /usr/lib/postgresql/16/lib/goldenmatch_pg.so /usr/lib/postgresql/16/lib/
COPY --from=builder /usr/share/postgresql/16/extension/goldenmatch_pg* /usr/share/postgresql/16/extension/

# Auto-create extension on database init
RUN echo "CREATE EXTENSION goldenmatch_pg;" > /docker-entrypoint-initdb.d/01-goldenmatch.sql

EXPOSE 5432
