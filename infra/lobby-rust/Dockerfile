# ssx3-lobby image: static musl binary on scratch, non-root.
# Build (from the repo root):
#   docker buildx build --platform linux/arm64,linux/amd64 -t ssx3-lobby:phase1 .
# Local single-arch test build:
#   docker build -t ssx3-lobby:phase1 .
ARG RUST_VERSION=1.90
FROM --platform=$BUILDPLATFORM rust:${RUST_VERSION}-alpine AS build
ARG TARGETPLATFORM
RUN apk add --no-cache musl-dev && \
    case "$TARGETPLATFORM" in \
      "linux/arm64") echo aarch64-unknown-linux-musl > /target-triple ;; \
      *) echo x86_64-unknown-linux-musl > /target-triple ;; \
    esac && \
    rustup target add $(cat /target-triple)
WORKDIR /src
COPY Cargo.toml Cargo.lock* ./
COPY crates ./crates
RUN TRIPLE=$(cat /target-triple) && \
    cargo build --release --target "$TRIPLE" && \
    cp "target/$TRIPLE/release/lobby" /lobby

FROM scratch
COPY --from=build /lobby /lobby
COPY lobby.example.toml /lobby.toml
# Non-root: scratch has no users; run as the standard nobody uid.
USER 65534:65534
EXPOSE 11000/tcp 10901/tcp 13505/tcp 3658-3757/udp
ENTRYPOINT ["/lobby", "/lobby.toml"]
