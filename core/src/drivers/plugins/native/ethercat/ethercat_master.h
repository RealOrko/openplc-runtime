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
#include "soem/soem.h"

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

/**
 * @brief Exchange process data with all slaves
 *
 * Sends outputs to slaves and receives inputs.
 *
 * @param logger Plugin logger instance
 * @return Working counter value from receive, or -1 on error
 */
int ecat_master_exchange_processdata(plugin_logger_t *logger);

/**
 * @brief Get the expected working counter for the bus
 *
 * Calculated as: outputsWKC * 2 + inputsWKC for group 0.
 *
 * @return Expected WKC value
 */
int ecat_master_get_expected_wkc(void);

/**
 * @brief Get a pointer to a live SOEM slave descriptor
 *
 * @param position 1-based slave position on the bus
 * @return Pointer to ec_slavet, or NULL if position is invalid
 */
const ec_slavet *ecat_master_get_slave(int position);

/**
 * @brief Check if all slaves are in OPERATIONAL state
 *
 * @return 1 if operational, 0 otherwise
 */
int ecat_master_is_operational(void);

#endif /* ETHERCAT_MASTER_H */
