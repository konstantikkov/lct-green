# --- builder: compile LibreDWG (free DWG→DXF converter) ---------------------
FROM debian:bookworm-slim AS libredwg
# build-essential, not bare gcc: bookworm-slim ships no libc headers, so a
# lone gcc fails ./configure with "C compiler cannot create executables".
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*
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

# ODA File Converter — freeware of the Open Design Alliance ("all rights
# reserved", no redistribution grant), so it is NOT in this repository: the
# build downloads it from ODA itself. It reads DWG files LibreDWG cannot
# (Харьковский проезд: survey and lighting plans; 3-я Парковая; the
# Олимпийская reference plan). Without internet the build goes on and DWG
# falls back to LibreDWG. Build with --build-arg WITH_ODA=0 to skip it.
# The Linux build is a Qt application with only the xcb platform plugin, so
# it runs under a virtual X server (xvfb-run).
ARG WITH_ODA=1
ARG ODA_URL=https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb
RUN if [ "$WITH_ODA" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
      && if curl -fsSL -m 600 -o /tmp/oda.deb "$ODA_URL"; then \
           apt-get install -y --no-install-recommends xvfb xauth libgl1 libegl1 libxkbcommon0 \
             libxkbcommon-x11-0 libfontconfig1 libx11-6 libx11-xcb1 libpng16-16 libfreetype6 \
             libglib2.0-0 libdbus-1-3 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
             libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxcb-cursor0 \
             libsm6 libice6 /tmp/oda.deb \
           && printf '#!/bin/sh\nexec xvfb-run -a /usr/bin/ODAFileConverter "$@"\n' > /usr/local/bin/oda-convert \
           && chmod +x /usr/local/bin/oda-convert && rm -f /tmp/oda.deb \
           && echo "ODA File Converter installed"; \
         else echo "ODA File Converter not downloaded - DWG via LibreDWG only"; fi \
      && rm -rf /var/lib/apt/lists/*; \
    fi
# used only when the wrapper exists (detect_converter checks the file)
ENV ODA_FILE_CONVERTER_PATH=/usr/local/bin/oda-convert

# Optional CPU rendering worker. The portable bundle also works without this.
ARG WITH_BLENDER=1
RUN if [ "$WITH_BLENDER" = "1" ]; then apt-get update \
    && apt-get install -y --no-install-recommends blender \
    && rm -rf /var/lib/apt/lists/*; fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
