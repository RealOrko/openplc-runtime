#ifndef UNIX_SOCKET_H
#define UNIX_SOCKET_H

#define SOCKET_PATH "/run/runtime/plc_runtime.socket"
#define COMMAND_BUFFER_SIZE 8192
#define MAX_RESPONSE_SIZE 65536
#define MAX_CLIENTS 1

int setup_unix_socket(void);
void close_unix_socket(int server_fd);
void *unix_socket_thread(void *arg);

// Setter for the plugin driver (called by plc_main after driver creation)
void unix_socket_set_plugin_driver(void *driver);

#endif // UNIX_SOCKET_H
