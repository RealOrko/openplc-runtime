/**
 * @file ethercat_master.h
 * @brief EtherCAT Master SOEM Wrapper Interface
 *
 * Provides a high-level interface for initializing and managing the
 * EtherCAT master using the SOEM library. Handles network initialization,
 * slave scanning, topology validation, and state transitions.
 */

#ifndef ETHERCAT_MASTER_H
#define ETHERCAT_MASTER_H

#include "ethercat_config.h"
#include "plugin_logger.h"

/**
 * @brief Initialize the EtherCAT master
 *
 * Performs the complete initialization sequence:
 * 1. Open network interface via SOEM
 * 2. Scan the bus for slaves
 * 3. Validate topology against JSON configuration
 * 4. Map process data
 * 5. Configure Distributed Clocks
 * 6. Transition slaves to OPERATIONAL state
 *
 * @param config Parsed EtherCAT configuration
 * @param logger Plugin logger instance
 * @return 0 on success, -1 on failure
 */
int ecat_master_init(const ecat_config_t *config, plugin_logger_t *logger);

/**
 * @brief Validate bus topology against configuration
 *
 * Compares the slaves found on the bus with the expected configuration:
 * - Number of slaves must match
 * - For each slave: vendor_id and product_code must match
 *
 * @param config Parsed EtherCAT configuration
 * @param logger Plugin logger instance
 * @return 0 on success, -1 on mismatch
 */
int ecat_master_validate_topology(const ecat_config_t *config, plugin_logger_t *logger);

/**
 * @brief Close the EtherCAT master
 *
 * Transitions all slaves to INIT state and closes the network interface.
 *
 * @param logger Plugin logger instance
 */
void ecat_master_close(plugin_logger_t *logger);

#endif /* ETHERCAT_MASTER_H */
