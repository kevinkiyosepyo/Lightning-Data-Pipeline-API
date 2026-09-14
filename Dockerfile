# syntax=docker/dockerfile:1.7
#
# Multi-stage build for both services. Build once, target either binary:
#   docker build --target ingest -t lightning-ingest .
#   docker build --target api    -t lightning-api .
#
# Runtime images are distroless: no shell, no package manager, ~30 MB.

FROM rust:1.90-slim-bookworm AS chef
RUN cargo install cargo-chef --locked
WORKDIR /app

# --- Dependency planning (cached unless Cargo.toml/lock change) -------------
FROM chef AS planner
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
RUN cargo chef prepare --recipe-path recipe.json

# --- Build -------------------------------------------------------------------
FROM chef AS builder
COPY --from=planner /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
COPY live.html ./live.html
RUN cargo build --release --workspace \
 && strip target/release/lightning-ingest target/release/lightning-api

# --- Runtime: ingest ---------------------------------------------------------
FROM gcr.io/distroless/cc-debian12:nonroot AS ingest
COPY --from=builder /app/target/release/lightning-ingest /usr/local/bin/lightning-ingest
USER nonroot
ENTRYPOINT ["/usr/local/bin/lightning-ingest"]

# --- Runtime: api ------------------------------------------------------------
FROM gcr.io/distroless/cc-debian12:nonroot AS api
COPY --from=builder /app/target/release/lightning-api /usr/local/bin/lightning-api
USER nonroot
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/lightning-api"]
