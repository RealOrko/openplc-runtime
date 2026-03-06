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

void *watchdog_thread(void *arg)
{
    (void)arg;
    long last = atomic_load(&plc_heartbeat);

    while (1)
    {
        sleep(2); // Watch every 2 seconds

        PLCState current_state = plc_get_state();
        if (current_state != PLC_STATE_RUNNING)
        {
            // Reset tracking when not running so we get a fresh
            // baseline when the PLC starts again
            if (current_state == PLC_STATE_ERROR)
            {
                last = 0;
                atomic_store(&plc_heartbeat, 0);
            }
            continue;
        }

        long now = atomic_load(&plc_heartbeat);
        if (now == last)
        {
            log_error("Watchdog: No heartbeat detected - PLC program is unresponsive");
            log_error("The loaded PLC program may contain an infinite loop. "
                      "Upload a corrected program to recover.");

            // Transition to ERROR state instead of killing the process.
            // This keeps the runtime alive so the webserver can still
            // communicate with it and upload a new program.
            plc_force_error_state();
            continue;
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
