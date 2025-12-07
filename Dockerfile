# Stage 1: Optimized Python Runtime (Cached indefinitely)
FROM python:3.12-slim AS python-optimized
RUN set -e && \
    PYTHON=/usr/local/lib/python3.12 && \
    (pip uninstall -y pip || :) && \
    find /usr/local -type d -name "__pycache__" -exec rm -rf {} + && \
    rm -rf \
    /usr/local/lib/pkgconfig /usr/local/include \
    $PYTHON/lib-dynload/_codecs_*.so \
    $PYTHON/config-3* \
    $PYTHON/doctest.py \
    $PYTHON/ensurepip \
    /usr/local/bin/idle* $PYTHON/idlelib \
    /usr/local/bin/2to3* $PYTHON/lib2to3 \
    $PYTHON/pdb.py \
    $PYTHON/lib-dynload/_tkinter* $PYTHON/tkinter \
    $PYTHON/lib-dynload/*test*.so \
    $PYTHON/turtle* \
    $PYTHON/venv
# $PYTHON/unittest \
# pyarrow.compute requires pydoc via numpydoc's docscrape.py
# $PYTHON/pydoc* /usr/local/bin/pydoc* \

# Stage 2: Build Application (Cached based on dependencies/code)
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Pre-download NLTK data
RUN ./.venv/bin/python -m nltk.downloader -d /app/nltk_data punkt_tab

# Stage 3: Final Image
FROM busybox:1.37-glibc

# Create user "app"
RUN echo "app:x:1000:1000:app:/app:/bin/sh" >> /etc/passwd && \
    echo "app:x:1000:" >> /etc/group

COPY --from=python-optimized /usr/local /usr/local
COPY --from=python-optimized /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

ENV LIB=/usr/lib/x86_64-linux-gnu
COPY --from=python-optimized \
    $LIB/libz.so.1 \
    $LIB/libzstd.so.1 \
    $LIB/libreadline.so.8 $LIB/libtinfo.so.6 \
    $LIB/libssl.so.3 \
    $LIB/libcrypto.so.3 \
    $LIB/libffi.so.8 \
    $LIB/libgcc_s.so.1 \
    $LIB/libstdc++.so.6 \
    $LIB/libdl.so.2 \
    $LIB/librt.so.1 \
    $LIB/libsqlite3.so.0 \
    /usr/lib/

COPY --from=builder --chown=1000:1000 /app /app

USER app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV NLTK_DATA=/app/nltk_data

EXPOSE 8765
ENTRYPOINT []

CMD ["granian", "intake_bot.server:app", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8765"]
