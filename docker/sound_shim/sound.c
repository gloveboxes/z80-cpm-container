#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET socket_t;
#define INVALID_FD INVALID_SOCKET
#define close_socket closesocket
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int socket_t;
#define INVALID_FD (-1)
#define close_socket close
#endif

#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#define SAMPLE_RATE 48000
#define RING_FRAMES 9600
static ma_pcm_rb ring;

static void playback(ma_device *device, void *output, const void *input, ma_uint32 frames)
{
    (void)device;
    (void)input;
    float *out = output;
    memset(out, 0, frames * sizeof(float));
    while (frames) {
        ma_uint32 available = frames;
        void *data;
        if (ma_pcm_rb_acquire_read(&ring, &available, &data) != MA_SUCCESS || !available)
            break;
        memcpy(out, data, available * sizeof(float));
        ma_pcm_rb_commit_read(&ring, available);
        out += available;
        frames -= available;
    }
}

int main(int argc, char **argv)
{
    const char *port_text = argc > 1 ? argv[1] : "8766";
    char *end;
    long port = strtol(port_text, &end, 10);
    if (argc > 2 || *end || port < 1 || port > 65535) {
        fprintf(stderr, "usage: altair-sound [port]\n");
        return 2;
    }
#ifdef _WIN32
    WSADATA data;
    if (WSAStartup(MAKEWORD(2, 2), &data))
        return 1;
#endif
    if (ma_pcm_rb_init(ma_format_f32, 1, RING_FRAMES, NULL, NULL, &ring) != MA_SUCCESS) {
        fprintf(stderr, "sound shim: cannot create audio buffer\n");
        return 1;
    }
    ma_device_config config = ma_device_config_init(ma_device_type_playback);
    config.playback.format = ma_format_f32;
    config.playback.channels = 1;
    config.sampleRate = SAMPLE_RATE;
    config.dataCallback = playback;
    ma_device device;
    if (ma_device_init(NULL, &config, &device) != MA_SUCCESS) {
        fprintf(stderr, "sound shim: cannot open audio output device\n");
        ma_pcm_rb_uninit(&ring);
        return 1;
    }
    if (ma_device_start(&device) != MA_SUCCESS) {
        fprintf(stderr, "sound shim: cannot start audio output device\n");
        ma_device_uninit(&device);
        ma_pcm_rb_uninit(&ring);
        return 1;
    }
    fprintf(stderr, "sound shim: waiting for localhost:%ld\n", port);
    for (;;) {
        socket_t fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd == INVALID_FD) {
            fprintf(stderr, "sound shim: cannot create socket\n");
            break;
        }
        struct sockaddr_in address = {0};
        address.sin_family = AF_INET;
        address.sin_port = htons((unsigned short)port);
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (connect(fd, (struct sockaddr *)&address, sizeof(address))) {
            close_socket(fd);
            ma_sleep(500);
            continue;
        }
        unsigned char bytes[1920 + sizeof(float)];
        size_t used = 0;
        int received = 0;
        for (;;) {
            int count = recv(fd, (char *)bytes + used, (int)(sizeof(bytes) - used), 0);
            if (count <= 0)
                break;
            if (!received) {
                fprintf(stderr, "sound shim: receiving audio\n");
                received = 1;
            }
            used += (size_t)count;
            size_t frames = used / sizeof(float);
            size_t remaining = frames;
            size_t offset = 0;
            while (remaining) {
                ma_uint32 writable = (ma_uint32)remaining;
                void *target;
                if (ma_pcm_rb_acquire_write(&ring, &writable, &target) != MA_SUCCESS || !writable)
                    break;
                memcpy(target, bytes + offset * sizeof(float), writable * sizeof(float));
                ma_pcm_rb_commit_write(&ring, writable);
                offset += writable;
                remaining -= writable;
            }
            used -= frames * sizeof(float);
            memmove(bytes, bytes + frames * sizeof(float), used);
        }
        close_socket(fd);
        if (received)
            fprintf(stderr, "sound shim: disconnected; retrying\n");
        ma_sleep(500);
    }
    ma_device_uninit(&device);
    ma_pcm_rb_uninit(&ring);
#ifdef _WIN32
    WSACleanup();
#endif
    return 1;
}
