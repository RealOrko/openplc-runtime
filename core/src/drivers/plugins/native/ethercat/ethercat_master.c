/**
 * @file ethercat_master.c
 * @brief EtherCAT Master SOEM Wrapper Implementation
 *
 * Wraps the SOEM library to provide high-level EtherCAT master operations:
 * network initialization, slave scanning, topology validation against
 * the JSON configuration, SDO writes, state machine management, and
 * slave recovery.
 *
 * Uses the ecx_* context-based API from SOEM 2.x.
 */

#include "ethercat_master.h"
#include "soem/soem.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/*
 * =============================================================================
 * SOEM Context and IO Map
 * =============================================================================
 */

/** Maximum IO map size in bytes */
#define ECAT_IOMAP_SIZE 4096

/** Number of retries when polling for OPERATIONAL state */
#define ECAT_OP_POLL_RETRIES 10

/** SOEM context - manages all bus state */
static ecx_contextt g_ecx_context;

/** IO map buffer for process data exchange */
static uint8 g_iomap[ECAT_IOMAP_SIZE];

/** Track whether SOEM was initialized (ec_init called) */
static int g_soem_initialized = 0;

/** Track total IOmap size after mapping */
static size_t g_iomap_used_size = 0;

/*
 * =============================================================================
 * Topology Validation
 * =============================================================================
 */

int ecat_master_validate_topology(const ecat_config_t *config, plugin_logger_t *logger)
{
    int found_count = g_ecx_context.slavecount;

    if (found_count != config->slave_count) {
        plugin_logger_error(logger,
            "Topology mismatch: expected %d slaves, found %d on the bus",
            config->slave_count, found_count);
        return -1;
    }

    for (int i = 0; i < config->slave_count; i++) {
        const ecat_slave_t *expected = &config->slaves[i];
        int pos = expected->position;

        if (pos < 1 || pos > found_count) {
            plugin_logger_error(logger,
                "Slave %d: position %d is out of range (1-%d)",
                i, pos, found_count);
            return -1;
        }

        ec_slavet *found = &g_ecx_context.slavelist[pos];

        if (found->eep_man != expected->vendor_id) {
            plugin_logger_error(logger,
                "Slave %d (%s) at position %d: vendor_id mismatch - "
                "expected 0x%08X, found 0x%08X",
                i, expected->name, pos,
                expected->vendor_id, found->eep_man);
            return -1;
        }

        if (found->eep_id != expected->product_code) {
            plugin_logger_error(logger,
                "Slave %d (%s) at position %d: product_code mismatch - "
                "expected 0x%08X, found 0x%08X",
                i, expected->name, pos,
                expected->product_code, found->eep_id);
            return -1;
        }

        plugin_logger_debug(logger,
            "Slave %d (%s) at position %d: topology OK "
            "(vendor=0x%08X, product=0x%08X)",
            i, expected->name, pos,
            found->eep_man, found->eep_id);
    }

    plugin_logger_info(logger, "Topology validation passed: %d slaves match configuration",
                       config->slave_count);
    return 0;
}

/*
 * =============================================================================
 * Phase 1: Open Interface, Scan Bus, Validate Topology
 * =============================================================================
 */

int ecat_master_open_and_scan(const ecat_config_t *config, plugin_logger_t *logger)
{
    /* Zero-initialize the SOEM context before use */
    memset(&g_ecx_context, 0, sizeof(g_ecx_context));
    memset(g_iomap, 0, sizeof(g_iomap));
    g_iomap_used_size = 0;

    /* Step 1: Initialize SOEM on the configured network interface */
    plugin_logger_info(logger, "Opening network interface: %s", config->master.interface);

    if (!ecx_init(&g_ecx_context, config->master.interface)) {
#if defined(__CYGWIN__) || defined(_WIN32)
        plugin_logger_error(logger,
            "Failed to initialize EtherCAT interface '%s'. "
            "Verify that Npcap (https://npcap.com) is installed and "
            "the interface name matches a network adapter (use "
            "'ipconfig' or Npcap's WlanHelper to list adapters).",
            config->master.interface);
#else
        plugin_logger_error(logger,
            "Failed to initialize EtherCAT interface '%s'. "
            "Check that the interface exists and the process has "
            "CAP_NET_RAW capability (or is running as root).",
            config->master.interface);
#endif
        return -1;
    }

    g_soem_initialized = 1;
    plugin_logger_info(logger, "Network interface opened successfully");

    /* Step 2: Scan the bus and enumerate slaves */
    plugin_logger_info(logger, "Scanning EtherCAT bus...");

    if (ecx_config_init(&g_ecx_context) <= 0) {
        plugin_logger_error(logger,
            "No EtherCAT slaves found on interface '%s'. "
            "Check cable connections and slave power.",
            config->master.interface);
        ecx_close(&g_ecx_context);
        g_soem_initialized = 0;
        return -1;
    }

    plugin_logger_info(logger, "Found %d slave(s) on the bus", g_ecx_context.slavecount);

    /* Log discovered slaves */
    for (int i = 1; i <= g_ecx_context.slavecount; i++) {
        ec_slavet *slave = &g_ecx_context.slavelist[i];
        plugin_logger_info(logger,
            "  [%d] %s - vendor=0x%08X, product=0x%08X, rev=0x%08X",
            i, slave->name, slave->eep_man, slave->eep_id, slave->eep_rev);
    }

    /* Step 3: Validate topology against JSON configuration */
    if (ecat_master_validate_topology(config, logger) != 0) {
        plugin_logger_error(logger,
            "Topology validation failed - aborting master initialization");
        ecx_close(&g_ecx_context);
        g_soem_initialized = 0;
        return -1;
    }

    /* Step 4: Wait for all slaves to reach PRE-OP state.
     * ecx_config_init() requests PRE-OP but does not wait for the
     * transition to complete. Slaves need to be in PRE-OP before
     * mailbox communication (SDO writes) can work. */
    plugin_logger_info(logger, "Waiting for slaves to reach PRE-OP state...");

    ecx_statecheck(&g_ecx_context, 0, EC_STATE_PRE_OP, EC_TIMEOUTSTATE * 4);
    ecx_readstate(&g_ecx_context);

    int all_preop = 1;
    for (int i = 1; i <= g_ecx_context.slavecount; i++) {
        ec_slavet *slave = &g_ecx_context.slavelist[i];
        if (slave->state < EC_STATE_PRE_OP) {
            plugin_logger_error(logger,
                "Slave %d (%s) failed to reach PRE-OP (state=0x%04X, ALstatus=0x%04X)",
                i, slave->name, slave->state, slave->ALstatuscode);
            all_preop = 0;
        }
    }

    if (!all_preop) {
        plugin_logger_error(logger, "Not all slaves reached PRE-OP - aborting");
        ecx_close(&g_ecx_context);
        g_soem_initialized = 0;
        return -1;
    }

    plugin_logger_info(logger, "All slaves in PRE-OP state");

    return 0;
}

/*
 * =============================================================================
 * Phase 2: SDO Configuration
 * =============================================================================
 */

int ecat_master_write_sdos(int slave_pos, const ecat_sdo_config_t *sdos,
                           int sdo_count, plugin_logger_t *logger)
{
    if (!g_soem_initialized) {
        plugin_logger_error(logger, "Cannot write SDOs: SOEM not initialized");
        return -1;
    }

    if (slave_pos < 1 || slave_pos > g_ecx_context.slavecount) {
        plugin_logger_error(logger, "Invalid slave position %d for SDO write", slave_pos);
        return -1;
    }

    if (sdo_count == 0)
        return 0;

    int written = 0;

    for (int i = 0; i < sdo_count; i++) {
        const ecat_sdo_config_t *sdo = &sdos[i];

        /* Parse index from hex string */
        uint16_t index = (uint16_t)strtol(sdo->index, NULL, 16);

        /* Determine data size from data type */
        ecat_data_type_t dt = sdo->parsed_type;
        int size = ecat_data_type_size(dt);
        if (size <= 0) {
            plugin_logger_warn(logger,
                "Slave %d SDO 0x%04X:%d: unknown data type '%s', assuming 4 bytes",
                slave_pos, index, sdo->subindex, sdo->data_type);
            size = 4;
            dt = ECAT_DTYPE_INT32;
        }

        /* Encode the double value into the correct wire type */
        uint8_t value_buf[8];
        memset(value_buf, 0, sizeof(value_buf));

        switch (dt) {
        case ECAT_DTYPE_BOOL:
        case ECAT_DTYPE_UINT8:  { uint8_t  v = (uint8_t)sdo->value;  memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_INT8:   { int8_t   v = (int8_t)sdo->value;   memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_UINT16: { uint16_t v = (uint16_t)sdo->value; memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_INT16:  { int16_t  v = (int16_t)sdo->value;  memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_UINT32: { uint32_t v = (uint32_t)sdo->value; memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_INT32:  { int32_t  v = (int32_t)sdo->value;  memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_UINT64: { uint64_t v = (uint64_t)sdo->value; memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_INT64:  { int64_t  v = (int64_t)sdo->value;  memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_REAL32: { float    v = (float)sdo->value;    memcpy(value_buf, &v, sizeof(v)); break; }
        case ECAT_DTYPE_REAL64: { double   v = sdo->value;           memcpy(value_buf, &v, sizeof(v)); break; }
        default:                { int32_t  v = (int32_t)sdo->value;  memcpy(value_buf, &v, sizeof(v)); break; }
        }

        if (dt == ECAT_DTYPE_REAL32 || dt == ECAT_DTYPE_REAL64) {
            plugin_logger_debug(logger,
                "Slave %d: writing SDO 0x%04X:%d = %g (%s, %d bytes)",
                slave_pos, index, sdo->subindex, sdo->value, sdo->data_type, size);
        } else {
            plugin_logger_debug(logger,
                "Slave %d: writing SDO 0x%04X:%d = %lld (%s, %d bytes)",
                slave_pos, index, sdo->subindex, (long long)(int64_t)sdo->value,
                sdo->data_type, size);
        }

        int wkc = ecx_SDOwrite(&g_ecx_context, (uint16)slave_pos,
                                index, sdo->subindex,
                                FALSE, size, value_buf, EC_TIMEOUTRXM);

        if (wkc <= 0) {
            plugin_logger_warn(logger,
                "Slave %d SDO 0x%04X:%d write failed (wkc=%d, name='%s')",
                slave_pos, index, sdo->subindex, wkc, sdo->name);
        } else {
            plugin_logger_debug(logger,
                "Slave %d SDO 0x%04X:%d write OK (name='%s')",
                slave_pos, index, sdo->subindex, sdo->name);
            written++;
        }
    }

    plugin_logger_info(logger, "Slave %d: %d/%d SDOs written successfully",
                       slave_pos, written, sdo_count);
    return written;
}

/*
 * =============================================================================
 * Phase 3: Process Data Mapping + Distributed Clocks
 * =============================================================================
 */

int ecat_master_configure(const ecat_config_t *config, plugin_logger_t *logger)
{
    if (!g_soem_initialized) {
        plugin_logger_error(logger, "Cannot configure: SOEM not initialized");
        return -1;
    }

    /* Step 4: Map process data (IO map) */
    plugin_logger_info(logger, "Mapping process data...");

    ecx_config_map_group(&g_ecx_context, &g_iomap, 0);

    ec_groupt *grp = &g_ecx_context.grouplist[0];

    /* Check that total I/O fits in the IOmap buffer */
    uint32_t total_io = (uint32_t)grp->Obytes + (uint32_t)grp->Ibytes;
    if (total_io > ECAT_IOMAP_SIZE) {
        plugin_logger_error(logger, "IOmap overflow: need %u bytes, have %d",
                            total_io, ECAT_IOMAP_SIZE);
        return -1;
    }

    g_iomap_used_size = (size_t)total_io;

    plugin_logger_info(logger, "IO map: %d output bytes, %d input bytes, %d segments",
                       grp->Obytes, grp->Ibytes, grp->nsegments);

    /* Step 5: Configure Distributed Clocks */
    plugin_logger_info(logger, "Configuring Distributed Clocks...");
    ecx_configdc(&g_ecx_context);

    return 0;
}

/*
 * =============================================================================
 * Phase 4: Transition to OPERATIONAL
 * =============================================================================
 */

int ecat_master_transition_to_op(plugin_logger_t *logger)
{
    if (!g_soem_initialized) {
        plugin_logger_error(logger, "Cannot transition: SOEM not initialized");
        return -1;
    }

    /* Step 6: Wait for SAFE_OP after config */
    plugin_logger_info(logger, "Waiting for SAFE_OP state...");

    ecx_statecheck(&g_ecx_context, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * 4);

    /* Read back actual states */
    ecx_readstate(&g_ecx_context);
    if (g_ecx_context.slavelist[0].state != EC_STATE_SAFE_OP) {
        plugin_logger_error(logger,
            "Not all slaves reached SAFE_OP state (current state: 0x%04X)",
            g_ecx_context.slavelist[0].state);

        /* Log individual slave states for debugging */
        for (int i = 1; i <= g_ecx_context.slavecount; i++) {
            ec_slavet *slave = &g_ecx_context.slavelist[i];
            if (slave->state != EC_STATE_SAFE_OP) {
                plugin_logger_error(logger,
                    "  Slave %d (%s): state=0x%04X, ALstatuscode=0x%04X",
                    i, slave->name, slave->state, slave->ALstatuscode);
            }
        }

        return -1;
    }

    plugin_logger_info(logger, "All slaves in SAFE_OP state");

    /* Step 7: Send initial process data and request OPERATIONAL */
    plugin_logger_info(logger, "Requesting OPERATIONAL state...");

    /* Send one round of process data to make slave outputs happy */
    ecx_send_processdata(&g_ecx_context);
    ecx_receive_processdata(&g_ecx_context, EC_TIMEOUTRET);

    /* Request OP state */
    g_ecx_context.slavelist[0].state = EC_STATE_OPERATIONAL;
    ecx_writestate(&g_ecx_context, 0);

    /* Poll for OP state with process data exchange between checks */
    int op_reached = 0;
    for (int retry = 0; retry < ECAT_OP_POLL_RETRIES; retry++) {
        ecx_send_processdata(&g_ecx_context);
        ecx_receive_processdata(&g_ecx_context, EC_TIMEOUTRET);
        ecx_statecheck(&g_ecx_context, 0, EC_STATE_OPERATIONAL,
                        EC_TIMEOUTSTATE / ECAT_OP_POLL_RETRIES);

        if (g_ecx_context.slavelist[0].state == EC_STATE_OPERATIONAL) {
            op_reached = 1;
            break;
        }
    }

    if (!op_reached) {
        plugin_logger_error(logger,
            "Not all slaves reached OPERATIONAL state after %d retries",
            ECAT_OP_POLL_RETRIES);

        /* Log individual slave states for debugging */
        ecx_readstate(&g_ecx_context);
        for (int i = 1; i <= g_ecx_context.slavecount; i++) {
            ec_slavet *slave = &g_ecx_context.slavelist[i];
            if (slave->state != EC_STATE_OPERATIONAL) {
                plugin_logger_error(logger,
                    "  Slave %d (%s): state=0x%04X, ALstatuscode=0x%04X",
                    i, slave->name, slave->state, slave->ALstatuscode);
            }
        }

        return -1;
    }

    plugin_logger_info(logger, "EtherCAT master operational with %d slave(s)",
                       g_ecx_context.slavecount);

    return 0;
}

/*
 * =============================================================================
 * Master Close
 * =============================================================================
 */

void ecat_master_close(plugin_logger_t *logger)
{
    if (!g_soem_initialized) {
        plugin_logger_debug(logger, "SOEM not initialized, nothing to close");
        return;
    }

    /* Transition all slaves to INIT state */
    plugin_logger_info(logger, "Transitioning slaves to INIT state...");
    g_ecx_context.slavelist[0].state = EC_STATE_INIT;
    ecx_writestate(&g_ecx_context, 0);

    /* Close the network interface */
    ecx_close(&g_ecx_context);
    g_soem_initialized = 0;

    /* Clear IO map */
    memset(g_iomap, 0, sizeof(g_iomap));
    g_iomap_used_size = 0;

    plugin_logger_info(logger, "EtherCAT master closed");
}

/*
 * =============================================================================
 * Process Data and State Access
 * =============================================================================
 */

int ecat_master_exchange_processdata(int timeout_us)
{
    ecx_send_processdata(&g_ecx_context);
    int wkc = ecx_receive_processdata(&g_ecx_context,
                                       (timeout_us > 0) ? timeout_us : EC_TIMEOUTRET);
    return wkc;
}

int ecat_master_get_expected_wkc(void)
{
    ec_groupt *grp = &g_ecx_context.grouplist[0];
    return (grp->outputsWKC * 2) + grp->inputsWKC;
}

const ec_slavet *ecat_master_get_slave(int position)
{
    if (position < 1 || position > g_ecx_context.slavecount)
        return NULL;
    return &g_ecx_context.slavelist[position];
}

int ecat_master_is_operational(void)
{
    if (!g_soem_initialized)
        return 0;
    return (g_ecx_context.slavelist[0].state == EC_STATE_OPERATIONAL) ? 1 : 0;
}

uint16_t ecat_master_get_slave_state(int position)
{
    if (position < 1 || position > g_ecx_context.slavecount)
        return 0;
    return g_ecx_context.slavelist[position].state;
}

int ecat_master_request_state(int position, uint16_t state, plugin_logger_t *logger)
{
    if (position < 1 || position > g_ecx_context.slavecount) {
        plugin_logger_error(logger, "Invalid slave position %d for state request", position);
        return -1;
    }

    g_ecx_context.slavelist[position].state = state;
    ecx_writestate(&g_ecx_context, (uint16)position);

    plugin_logger_debug(logger, "Requested state 0x%04X for slave %d", state, position);
    return 0;
}

/*
 * =============================================================================
 * Slave Recovery
 * =============================================================================
 */

int ecat_master_recover_slave(int position, plugin_logger_t *logger)
{
    if (position < 1 || position > g_ecx_context.slavecount) {
        plugin_logger_error(logger, "Invalid slave position %d for recovery", position);
        return -1;
    }

    ec_slavet *slave = &g_ecx_context.slavelist[position];
    uint16_t current_state = slave->state;

    if (current_state == EC_STATE_OPERATIONAL) {
        /* Already operational */
        return 1;
    }

    if (current_state == (EC_STATE_SAFE_OP + EC_STATE_ERROR)) {
        /* SAFE_OP + ERROR: ACK the error, then request OP */
        plugin_logger_info(logger,
            "Slave %d (%s): SAFE_OP+ERROR (ALstatus=0x%04X), sending ACK",
            position, slave->name, slave->ALstatuscode);

        slave->state = EC_STATE_SAFE_OP + EC_STATE_ACK;
        ecx_writestate(&g_ecx_context, (uint16)position);

        /* Now request OP */
        slave->state = EC_STATE_OPERATIONAL;
        ecx_writestate(&g_ecx_context, (uint16)position);

        /* Check if it worked */
        ecx_statecheck(&g_ecx_context, (uint16)position,
                        EC_STATE_OPERATIONAL, EC_TIMEOUTRET);

        if (slave->state == EC_STATE_OPERATIONAL) {
            plugin_logger_info(logger, "Slave %d (%s): recovered to OP",
                               position, slave->name);
            return 1;
        }
        return 0;
    }

    if (current_state == EC_STATE_SAFE_OP) {
        /* SAFE_OP: just request OP */
        plugin_logger_info(logger, "Slave %d (%s): in SAFE_OP, requesting OP",
                           position, slave->name);

        slave->state = EC_STATE_OPERATIONAL;
        ecx_writestate(&g_ecx_context, (uint16)position);

        ecx_statecheck(&g_ecx_context, (uint16)position,
                        EC_STATE_OPERATIONAL, EC_TIMEOUTRET);

        if (slave->state == EC_STATE_OPERATIONAL) {
            plugin_logger_info(logger, "Slave %d (%s): recovered to OP",
                               position, slave->name);
            return 1;
        }
        return 0;
    }

    if (current_state > EC_STATE_NONE) {
        /* Lower state but still present: try full reconfiguration */
        plugin_logger_info(logger,
            "Slave %d (%s): state=0x%04X, attempting reconfig",
            position, slave->name, current_state);

        if (ecx_reconfig_slave(&g_ecx_context, (uint16)position, EC_TIMEOUTRET)) {
            slave->islost = FALSE;
            plugin_logger_info(logger, "Slave %d (%s): reconfigured", position, slave->name);

            /* After reconfig, check if it reached OP */
            ecx_statecheck(&g_ecx_context, (uint16)position,
                            EC_STATE_OPERATIONAL, EC_TIMEOUTRET);
            if (slave->state == EC_STATE_OPERATIONAL)
                return 1;
            return 0;
        }
        return 0;
    }

    /* EC_STATE_NONE: slave is lost, try recover */
    if (!slave->islost) {
        ecx_statecheck(&g_ecx_context, (uint16)position,
                        EC_STATE_OPERATIONAL, EC_TIMEOUTRET);
        if (slave->state == EC_STATE_NONE) {
            slave->islost = TRUE;
            plugin_logger_warn(logger, "Slave %d (%s): marked as lost",
                               position, slave->name);
        }
        return 0;
    }

    /* Slave was marked lost - try to recover */
    if (ecx_recover_slave(&g_ecx_context, (uint16)position, EC_TIMEOUTRET)) {
        slave->islost = FALSE;
        plugin_logger_info(logger, "Slave %d (%s): recovered from lost state",
                           position, slave->name);
        return 1;
    }

    return 0;
}

void ecat_master_read_states(void)
{
    if (g_soem_initialized)
        ecx_readstate(&g_ecx_context);
}

/*
 * =============================================================================
 * IOmap Access
 * =============================================================================
 */

uint8_t *ecat_master_get_iomap(void)
{
    if (!g_soem_initialized)
        return NULL;
    return g_iomap;
}

size_t ecat_master_get_iomap_size(void)
{
    return g_iomap_used_size;
}

int ecat_master_get_slave_count(void)
{
    if (!g_soem_initialized)
        return 0;
    return g_ecx_context.slavecount;
}
