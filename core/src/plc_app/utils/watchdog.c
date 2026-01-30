#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include "../plc_state_manager.h"
#include "log.h"
#include "utils.h"
#include "watchdog.h"

atomic_long plc_heartbeat;
extern PLCState plc_state;

void *watchdog_thread(void *arg)
{
    (void)arg;
    long last = atomic_load(&plc_heartbeat);

    while (1)
    {
        sleep(2); // Watch every 2 seconds

        if (plc_get_state() != PLC_STATE_RUNNING)
        {
            continue; // Only monitor when PLC is running
        }

        long now = atomic_load(&plc_heartbeat);
        if (now == last)
        {
            // Use stderr to ensure visibility and avoid lockups in log system
            time_t now_time = time(NULL);
            struct tm t;
            gmtime_r(&now_time, &t);
            char time_buf[20];
            strftime(time_buf, sizeof(time_buf), "%Y-%m-%d %H:%M:%S", &t);
            fprintf(stderr, "[%s] [ERROR] Watchdog: No heartbeat! PLC unresponsive.\n", time_buf);
            exit(EXIT_FAILURE);
        }

        last = now;
    }

    return NULL;
}

int watchdog_init(void)
{
    pthread_t wd_thread;
    if (pthread_create(&wd_thread, NULL, watchdog_thread, NULL) != 0)
    {
        log_error("Failed to create watchdog thread");
        return -1;
    }
    pthread_detach(wd_thread); // Detach the thread to avoid memory leaks
    return 0;
}
