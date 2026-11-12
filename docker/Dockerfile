FROM debian:bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake python3 python3-aiohttp z80asm z80dasm cpmtools libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/altair
COPY sbc/ sbc/
COPY runtime/run.py runtime/terminal_bridge.py runtime/debug_session.py runtime/debug_terminal.py runtime/
COPY docker/speaker_stream.c docker/speaker_stream.c
COPY docker/server.py docker/server.py
COPY terminal/ terminal/
COPY disks/ disks/
RUN cp docker/speaker_stream.c sbc/src/cpm/dcc_io_adapter/host/PortDrivers/speaker_audio.c \
    && sed -i '/speaker_io_init(SPEAKER_IO_CLOCK_AUDIO);/a\    speaker_io_input();' \
       sbc/src/cpm/dcc_io_adapter/src/dcc_debug_io_adapter.c \
    && python3 runtime/run.py --prepare-only --no-audio
ENV ALTAIR_WEB_PORT=8080 ALTAIR_SOUND_PORT=8766 ALTAIR_CONSOLE_PORT=8767
EXPOSE 8080 8081 8766 8767
ENTRYPOINT ["python3", "docker/server.py"]
