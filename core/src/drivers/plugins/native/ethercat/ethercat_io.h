/**
 * @file ethercat_io.h
 * @brief EtherCAT I/O Module — IEC location parsing, channel mapping, and process data exchange
 *
 * Bridges the SOEM IOmap and the OpenPLC runtime I/O buffers.
 * Provides:
 *  - IEC 61131-3 location string parser (%IX0.0, %QW3, etc.)
 *  - Channel map builder that links each configured channel to its
 *    IOmap byte/bit and PLC buffer position
 *  - Per-cycle read/write helpers called from cycle_start/cycle_end
 */

#ifndef ETHERCAT_IO_H
#define ETHERCAT_IO_H

#include <stdint.h>
#include "ethercat_config.h"
#include "plugin_types.h"
#include "plugin_logger.h"
#include "soem/soem.h"

/* Maximum entries in a single direction of the channel map */
#define ECAT_MAX_MAP_ENTRIES 256

/**
 * @brief IEC 61131-3 data size qualifiers
 */
typedef enum {
    IEC_SIZE_BIT,    /* X — single bit   */
    IEC_SIZE_BYTE,   /* B — 1 byte       */
    IEC_SIZE_WORD,   /* W — 2 bytes      */
    IEC_SIZE_DWORD,  /* D — 4 bytes      */
    IEC_SIZE_LWORD   /* L — 8 bytes      */
} iec_size_t;

/**
 * @brief IEC 61131-3 direction qualifiers
 */
typedef enum {
    IEC_DIR_INPUT,   /* I — physical input  */
    IEC_DIR_OUTPUT   /* Q — physical output */
} iec_dir_t;

/**
 * @brief Parsed IEC location — result of parsing a string like "%IX0.3"
 */
typedef struct {
    iec_dir_t  direction;   /* I or Q            */
    iec_size_t size;        /* X, B, W, D, L     */
    int        byte_index;  /* byte address       */
    int        bit_index;   /* bit within byte (X only, 0-7; -1 otherwise) */
} iec_location_t;

/**
 * @brief Single entry in the channel map
 *
 * Ties one PDO entry (IOmap side) to one PLC buffer position.
 * Uses an offset relative to the IOmap base instead of an absolute pointer,
 * allowing the same map to work with both the real IOmap and a shadow buffer.
 */
typedef struct {
    /* IOmap side */
    size_t   iomap_offset;     /* byte offset from IOmap base            */
    int      iomap_bit_offset; /* bit offset within the byte (0-7)       */
    uint8_t  bit_length;       /* channel width in bits                  */

    /* PLC side */
    iec_size_t      size;      /* IEC size qualifier                     */
    int             byte_index;/* byte index into PLC buffer             */
    int             bit_index; /* bit index (IEC_SIZE_BIT only, else -1) */
    ecat_data_type_t data_type;/* CoE data type from the PDO entry       */
} ecat_channel_map_entry_t;

/**
 * @brief Complete channel map — separate arrays for inputs and outputs
 */
typedef struct {
    ecat_channel_map_entry_t inputs[ECAT_MAX_MAP_ENTRIES];
    int                      input_count;
    ecat_channel_map_entry_t outputs[ECAT_MAX_MAP_ENTRIES];
    int                      output_count;
} ecat_channel_map_t;

/**
 * @brief Parse an IEC 61131-3 location string into its components
 *
 * Accepted format: %[IQ][XBWDL]<byte>[.<bit>]
 *   - Direction: I (input) or Q (output)
 *   - Size: X (bit), B (byte), W (word), D (dword), L (lword)
 *   - Byte: decimal byte address
 *   - Bit: optional, only valid for X size, 0-7
 *
 * @param loc_str  NUL-terminated IEC location string
 * @param loc      Output parsed location
 * @return 0 on success, -1 on parse error
 */
int ecat_io_parse_iec_location(const char *loc_str, iec_location_t *loc);

/**
 * @brief Build the channel map from configuration + live SOEM state
 *
 * Iterates every slave/channel in @p config, resolves the IOmap pointer
 * via SOEM slave data, parses the IEC location, and stores the mapping
 * for use in the per-cycle read/write functions.
 *
 * @param config  Parsed EtherCAT configuration
 * @param map     Output channel map (zeroed before population)
 * @param args    Runtime args (for buffer_size bounds check)
 * @param logger  Logger instance
 * @return 0 on success, -1 if no channels could be mapped
 */
int ecat_io_build_channel_map(const ecat_config_t *config,
                              ecat_channel_map_t *map,
                              plugin_runtime_args_t *args,
                              plugin_logger_t *logger);

/**
 * @brief Copy inputs from IOmap into PLC input buffers
 *
 * Called from cycle_start() after process data has been received.
 * The iomap_base parameter allows reading from either the real SOEM IOmap
 * or a shadow buffer, enabling decoupled EtherCAT and PLC cycles.
 *
 * @param map        Channel map built by ecat_io_build_channel_map()
 * @param iomap_base Base pointer of the IOmap buffer to read from
 * @param args       Runtime args with PLC buffer pointers
 */
void ecat_io_read_inputs(const ecat_channel_map_t *map,
                         const uint8_t *iomap_base,
                         plugin_runtime_args_t *args);

/**
 * @brief Copy PLC output buffers into IOmap
 *
 * Called from cycle_end() before the next process data send.
 * The iomap_base parameter allows writing to either the real SOEM IOmap
 * or a shadow buffer, enabling decoupled EtherCAT and PLC cycles.
 *
 * @param map        Channel map built by ecat_io_build_channel_map()
 * @param iomap_base Base pointer of the IOmap buffer to write to
 * @param args       Runtime args with PLC buffer pointers
 */
void ecat_io_write_outputs(const ecat_channel_map_t *map,
                           uint8_t *iomap_base,
                           plugin_runtime_args_t *args);

/*
 * =============================================================================
 * Transfer List — Pre-resolved I/O for fast per-cycle exchange
 * =============================================================================
 *
 * The transfer list resolves all pointer dereferences, NULL checks, and type
 * dispatches once at startup, producing a flat array of {plc_ptr, iomap_offset,
 * byte_count} entries.  The per-cycle functions then iterate this array with a
 * single branch (bit vs. non-bit) and a direct memcpy per entry.
 */

/**
 * @brief Single pre-resolved transfer entry
 *
 * All fields are resolved once by ecat_io_build_transfer_list() so that
 * the per-cycle functions need no switch, no NULL check, and no double
 * pointer dereference.
 */
typedef struct {
    void    *plc_ptr;           /* direct pointer to the PLC variable        */
    size_t   iomap_offset;      /* byte offset from IOmap base               */
    int      iomap_bit_offset;  /* bit offset within the byte (0-7)          */
    uint8_t  byte_count;        /* bytes to copy (1, 2, 4, or 8)            */
    bool     is_bit;            /* true for IEC_SIZE_BIT channels            */
} ecat_transfer_entry_t;

/**
 * @brief Complete transfer list — separate arrays for inputs and outputs
 */
typedef struct {
    ecat_transfer_entry_t inputs[ECAT_MAX_MAP_ENTRIES];
    int                   input_count;
    ecat_transfer_entry_t outputs[ECAT_MAX_MAP_ENTRIES];
    int                   output_count;
} ecat_transfer_list_t;

/**
 * @brief Build a transfer list from a channel map and runtime args
 *
 * Resolves each channel map entry into a direct {plc_ptr, iomap_offset,
 * byte_count} triple.  Entries whose PLC pointer is NULL (unmapped IEC
 * address) are silently skipped.
 *
 * Must be called after ecat_io_build_channel_map() and after glueVars()
 * has populated the image table pointers.
 *
 * @param map    Channel map built by ecat_io_build_channel_map()
 * @param xfer   Output transfer list (zeroed before population)
 * @param args   Runtime args with PLC buffer pointers
 * @param logger Logger instance
 * @return Number of entries resolved, or -1 on error
 */
int ecat_io_build_transfer_list(const ecat_channel_map_t *map,
                                ecat_transfer_list_t *xfer,
                                plugin_runtime_args_t *args,
                                plugin_logger_t *logger);

/**
 * @brief Fast per-cycle: copy IOmap inputs into PLC variables
 *
 * @param xfer       Transfer list built by ecat_io_build_transfer_list()
 * @param iomap_base Base pointer of the IOmap buffer
 */
void ecat_io_read_inputs_fast(const ecat_transfer_list_t *xfer,
                              const uint8_t *iomap_base);

/**
 * @brief Fast per-cycle: copy PLC variables into IOmap outputs
 *
 * @param xfer       Transfer list built by ecat_io_build_transfer_list()
 * @param iomap_base Base pointer of the IOmap buffer
 */
void ecat_io_write_outputs_fast(const ecat_transfer_list_t *xfer,
                                uint8_t *iomap_base);

#endif /* ETHERCAT_IO_H */
