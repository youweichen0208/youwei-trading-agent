# PostgreSQL 16 (pinned official alpine image) + pgBackRest, for Phase 1A
# production backups.
#
# pgBackRest is built from source on the SAME alpine release as the
# postgres base (musl), so swapping the postgres container to this image
# never changes libc/collation for an existing PGDATA volume (a glibc
# switch would require a dump/restore because of collation versions).
# Only the binary plus three small runtime libraries are added; the
# postgres binaries are byte-identical to the pinned base image.
#
# The binary serves two roles:
#   - archive_command ('pgbackrest archive-push') inside the postgres
#     container
#   - backup / restore / info commands from one-shot containers that
#     share the repo (and, for restore, the data) volume(s)
#
# Build:  docker build -f infra/images/postgres-pgbackrest.Dockerfile \
#           -t ghcr.io/youweichen0208/youwei-postgres:<tag> infra/images
# Pin the resulting registry digest in infra/compose/production.json and
# infra/upstreams.lock.yaml.

FROM alpine:3.24 AS builder
ARG PGBACKREST_VERSION=2.59.2
RUN apk add --no-cache \
        build-base meson ninja curl \
        libxml2-dev openssl-dev zlib-dev lz4-dev zstd-dev \
        bzip2-dev libpq-dev yaml-dev xz-dev
RUN curl -fsSL \
        "https://github.com/pgbackrest/pgbackrest/archive/refs/tags/release/${PGBACKREST_VERSION}.tar.gz" \
        | tar xz -C /tmp
WORKDIR /tmp/pgbackrest-release-${PGBACKREST_VERSION}
RUN meson setup build && ninja -C build && ./build/src/pgbackrest --version

FROM postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea
ARG PGBACKREST_VERSION=2.59.2
# libbz2 / liblzma / libyaml are not in the postgres image (alpine splits
# libbz2 out of the bzip2 tools package); libpq comes from the base image
# itself (/usr/local/lib, linked by the postgres client tools) and needs no
# extra package.
RUN apk add --no-cache libbz2 xz-libs yaml
COPY --from=builder \
    /tmp/pgbackrest-release-${PGBACKREST_VERSION}/build/src/pgbackrest \
    /usr/local/bin/pgbackrest
# Fail the build if any shared library is missing at runtime, and prove
# both binaries work; record the added package versions for the record.
RUN set -eux; \
    ldd /usr/local/bin/pgbackrest 2>&1 | tee /tmp/ldd.txt; \
    ! grep -q "Error loading" /tmp/ldd.txt; \
    pgbackrest --version; \
    postgres --version; \
    apk info -v libbz2 xz-libs yaml; \
    rm /tmp/ldd.txt
