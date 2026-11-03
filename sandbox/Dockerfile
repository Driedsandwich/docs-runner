FROM python:3.12-slim-bookworm AS rootfs
RUN apt-get update && apt-get install -y --no-install-recommends bash curl git nodejs npm ca-certificates && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 65532 runner && useradd --uid 65532 --gid 65532 --home-dir /work --no-create-home runner
COPY sandbox/init.py sandbox/probe.py /opt/
COPY sandbox/curlrc /etc/curlrc
COPY sandbox/pip.conf /etc/pip.conf
COPY sandbox/npmrc /etc/npmrc
COPY sandbox/gitconfig /etc/gitconfig
RUN mv /usr/bin/curl /usr/bin/curl.real
COPY sandbox/curl /usr/bin/curl
# A scratch final stage deliberately drops all inherited image ENV values.
FROM scratch
COPY --from=rootfs / /
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/python3", "-I", "-S", "/opt/init.py"]

