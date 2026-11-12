/* The SBC sound engine remains authoritative; stream its rendered PCM. */
#define _POSIX_C_SOURCE 200809L
#include "PortDrivers/speaker_audio.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define FRAMES 480
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_t worker;
static atomic_int running;
static int listener = -1;
static speaker_audio_render_fn renderer;

static void *stream_audio(void *unused)
{
    (void)unused;
    float samples[FRAMES];
    struct timespec deadline;
    int client = -1;
    clock_gettime(CLOCK_MONOTONIC, &deadline);
    while (atomic_load(&running)) {
        if (client < 0) {
            client = accept(listener, NULL, NULL);
            if (client >= 0)
                fcntl(client, F_SETFL, fcntl(client, F_GETFL) | O_NONBLOCK);
        }
        pthread_mutex_lock(&lock);
        renderer(samples, FRAMES);
        pthread_mutex_unlock(&lock);
        if (client >= 0 &&
            send(client, samples, sizeof(samples), MSG_NOSIGNAL) != sizeof(samples)) {
            close(client);
            client = -1;
        }
        deadline.tv_nsec += 10000000;
        if (deadline.tv_nsec >= 1000000000) {
            ++deadline.tv_sec;
            deadline.tv_nsec -= 1000000000;
        }
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL) == EINTR) {}
    }
    if (client >= 0)
        close(client);
    return NULL;
}

int speaker_audio_open(uint32_t sample_rate, speaker_audio_render_fn render)
{
    const char *port_text = getenv("ALTAIR_SOUND_PORT");
    char *end;
    long port = port_text ? strtol(port_text, &end, 10) : 8766;
    if (sample_rate != 48000 || (port_text && (*end || port < 1 || port > 65535))) {
        fprintf(stderr, "speaker stream: invalid rate or port\n");
        return 0;
    }
    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) {
        perror("speaker stream socket");
        return 0;
    }
    int reuse = 1;
    struct sockaddr_in address = {0};
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons((unsigned short)port);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) ||
        listen(listener, 1) || fcntl(listener, F_SETFL, O_NONBLOCK)) {
        perror("speaker stream listen");
        close(listener);
        listener = -1;
        return 0;
    }
    renderer = render;
    atomic_store(&running, 1);
    if (pthread_create(&worker, NULL, stream_audio, NULL)) {
        fprintf(stderr, "speaker stream: cannot start audio thread\n");
        close(listener);
        listener = -1;
        atomic_store(&running, 0);
        return 0;
    }
    fprintf(stderr, "speaker stream: listening on port %ld\n", port);
    return 1;
}

void speaker_audio_close(void)
{
    if (atomic_exchange(&running, 0)) {
        pthread_join(worker, NULL);
        close(listener);
        listener = -1;
    }
}

void speaker_audio_lock(void) { pthread_mutex_lock(&lock); }
void speaker_audio_unlock(void) { pthread_mutex_unlock(&lock); }
