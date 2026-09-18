# --- builder: compile LibreDWG (free DWG→DXF converter) ---------------------
FROM debian:bookworm-slim AS libredwg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc make curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -sL -o /tmp/libredwg.tar.gz \
      https://github.com/LibreDWG/libredwg/releases/download/0.13.3/libredwg-0.13.3.tar.gz \
 && tar xzf /tmp/libredwg.tar.gz -C /tmp \
 && cd /tmp/libredwg-0.13.3 \
 && ./configure --prefix=/opt/libredwg --disable-bindings --disable-python \
      --disable-write --enable-release \
 && make -j"$(nproc)" && make install

# --- runtime -----------------------------------------------------------------
FROM python:3.11-slim
WORKDIR /repo/apps/api
COPY --from=libredwg /opt/libredwg /opt/libredwg
ENV LIBREDWG_DWG2DXF_PATH=/opt/libredwg/bin/dwg2dxf \
    PATH="/opt/libredwg/bin:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
