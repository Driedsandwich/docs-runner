FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends squid ca-certificates && rm -rf /var/lib/apt/lists/*
USER proxy
ENTRYPOINT ["/usr/sbin/squid", "-N", "-f", "/tmp/squid.conf"]

