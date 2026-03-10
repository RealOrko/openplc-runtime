# Logging Normalization Plan

**Goal**: Unify all log output to a human-readable format for stdout while maintaining JSON for API responses.

**Target Format**:
```
[2024-01-30 12:00:00] [INFO] message
[2024-01-30 12:00:00] [WARN] warning message
[2024-01-30 12:00:00] [ERROR] error message
```

---

## Current State Analysis

### Problem

The codebase has **two parallel output mechanisms**:

1. **Formal logging system** (JSON output):
   - C side: `log_info()`, `log_error()`, etc. in `core/src/plc_app/utils/log.c`
   - Python side: `logger.info()`, `logger.error()`, etc.
   - Goes through UNIX socket, printed as JSON to stdout

2. **Direct printf/print calls** (human-readable):
   - C side: `printf()`, `fprintf(stderr)` scattered throughout code
   - Python side: `print()` statements in various modules

### Current Output (Mixed)

```
[PLUGIN]: Creating Python capsule for args
[PLUGIN]: Python capsule created successfully
[OPCUA INFO] OPC UA Plugin initializing...
{"timestamp": "1769785673", "level": "INFO", "message": "Logging initialized", "id": 8}
{"timestamp": "1769785673", "level": "INFO", "message": "Buffer accessor created", "id": 9}
```

---

## Implementation Plan

### Phase 1: Update Python Logging System

**Goal**: Change stdout output from JSON to human-readable format.

#### Files to Modify

| File | Change |
|------|--------|
| `webserver/logger/formatter.py` | Add `HumanReadableFormatter` class |
| `webserver/logger/__init__.py` | Use `HumanReadableFormatter` for `StreamHandler` (line 43) |
| `webserver/logger/logger.py` | Use `HumanReadableFormatter` for `StreamHandler` (line 19) |

#### HumanReadableFormatter Implementation

```python
class HumanReadableFormatter(logging.Formatter):
    """Format log records as human-readable strings for stdout."""

    def format(self, record):
        msg = record.getMessage()

        # Try to detect pre-formatted JSON (from C runtime)
        try:
            log_entry = json.loads(msg)
            timestamp = log_entry.get("timestamp", "")
            level = log_entry.get("level", record.levelname)
            message = log_entry.get("message", msg)

            # Convert Unix timestamp to human-readable
            if timestamp and str(timestamp).isdigit():
                dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
                timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            elif timestamp:
                # Handle ISO 8601 format
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        except (json.JSONDecodeError, ValueError):
            # Not JSON - use record fields directly
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            level = record.levelname
            message = msg

        return f"[{timestamp}] [{level}] {message}"
```

#### Changes in `__init__.py` (line 43)

```python
# Current:
stream_handler.setFormatter(JsonFormatter())

# New:
stream_handler.setFormatter(HumanReadableFormatter())
```

#### Changes in `logger.py` (line 19)

```python
# Current:
stream_handler.setFormatter(JsonFormatter())

# New:
stream_handler.setFormatter(HumanReadableFormatter())
```

**Note**: `BufferHandler` keeps using `JsonFormatter` for API responses.

---

### Phase 2: Convert C printf/fprintf to log_*() calls

#### Files and Occurrence Count

| File | printf | fprintf | Total |
|------|--------|---------|-------|
| `core/src/drivers/plugin_driver.c` | 41 | 32 | 73 |
| `core/src/drivers/plugin_config.c` | 2 | 0 | 2 |
| `core/src/drivers/plugins/native/plugin_logger.c` | 1 | 3 | 4 |
| `core/src/plc_app/plc_main.c` | 0 | 1 | 1 |
| `core/src/plc_app/journal_buffer.c` | 0 | 2 | 2 |
| `core/src/plc_app/utils/watchdog.c` | 0 | 1 | 1 |
| `core/src/plc_app/python_loader.c` | 0 | 1 | 1 |
| **Total** | **44** | **40** | **84** |

**Skip** (not production code):
- `core/src/drivers/plugins/native/examples/test_plugin_loader.c` (test file)
- `core/src/drivers/README.md` (documentation examples)

#### Conversion Rules

| Original Pattern | Converted To |
|------------------|--------------|
| `printf("[PLUGIN]: %s\n", msg)` | `log_info("%s", msg)` |
| `printf("[PLUGIN]: ... successfully\n")` | `log_info("...")` |
| `fprintf(stderr, "[PLUGIN]: Error...\n")` | `log_error("...")` |
| `fprintf(stderr, "Failed to...\n")` | `log_error("Failed to...")` |
| `fprintf(stderr, "Warning:...\n")` | `log_warn("...")` |

#### Main File: `core/src/drivers/plugin_driver.c`

This file has the most occurrences. Key conversions:

```c
// Current:
printf("[PLUGIN]: Config file %s not found, copying from plugins_default.conf\n", config_file);

// Converted:
log_info("Config file %s not found, copying from plugins_default.conf", config_file);
```

```c
// Current:
fprintf(stderr, "[PLUGIN]: Error - driver is NULL\n");

// Converted:
log_error("Error - driver is NULL");
```

```c
// Current:
printf("[PLUGIN]: Plugin %s started successfully.\n", plugin->config.name);

// Converted:
log_info("Plugin %s started successfully", plugin->config.name);
```

**Note**: Need to include `log.h` header in files that don't already have it.

---

### Phase 3: Convert Python print() to logger.*() calls

#### Files and Occurrence Count

| File | print() calls | Notes |
|------|---------------|-------|
| `webserver/plugin_config_model.py` | 6 | Config file operations |
| `webserver/credentials.py` | 10 | Certificate generation |
| `webserver/config.py` | 2 | Env validation |
| `webserver/app.py` | 2 | Platform detection |
| `core/src/drivers/plugins/python/modbus_master/modbus_master_memory.py` | ~20 | Error messages |
| **Total** | **~40** | |

#### Conversion Rules

| Original Pattern | Converted To |
|------------------|--------------|
| `print(f"[PLUGIN]: {msg}")` | `logger.info(msg)` |
| `print(f"[PLUGIN]: Failed to {x}")` | `logger.error(f"Failed to {x}")` |
| `print(f"Warning: {msg}")` | `logger.warning(msg)` |
| `print(f"Error: {msg}")` | `logger.error(msg)` |
| `print(f"Successfully {x}")` | `logger.info(f"Successfully {x}")` |

#### Example: `webserver/plugin_config_model.py`

```python
# Current:
print(f"[PLUGIN]: Config file {file_path} not found, copying from {default_file}")

# Converted:
logger.info(f"Config file {file_path} not found, copying from {default_file}")
```

```python
# Current:
print(f"[PLUGIN]: Failed to copy {default_file}: {e}")

# Converted:
logger.error(f"Failed to copy {default_file}: {e}")
```

#### Example: `webserver/credentials.py`

```python
# Current:
print(f"Generating self-signed certificate for {self.hostname}...")

# Converted:
logger.info(f"Generating self-signed certificate for {self.hostname}...")
```

```python
# Current:
print(f"Certificate saved to {cert_path}")

# Converted:
logger.info(f"Certificate saved to {cert_path}")
```

**Note**: Each file needs to import the logger:
```python
from webserver.logger import get_logger
logger, _ = get_logger(__name__)
```

---

### Phase 4: Update Plugin Fallback Logging

#### File: `core/src/drivers/plugins/python/opcua/opcua_logging.py`

The fallback `print()` statements should match the standard format when runtime logging isn't available.

```python
# Current fallback:
print(f"[OPCUA INFO] {message}", file=sys.stdout)

# Updated fallback (matching standard format):
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
print(f"[{timestamp}] [INFO] {message}", file=sys.stdout)
```

Apply to all 4 fallback print statements (info, warn, error, debug).

---

## Implementation Order

1. **Phase 1** - Python logging formatter (3 files)
   - Lowest risk, immediate visible improvement
   - Test: Run runtime, verify JSON messages now appear human-readable

2. **Phase 3** - Python print conversions (~40 calls across 5 files)
   - Medium effort, easy to test
   - Test: Run runtime, verify print messages go through logger

3. **Phase 2** - C printf/fprintf conversions (~84 calls across 7 files)
   - Highest effort, requires rebuild
   - Test: Run runtime, verify all plugin messages use logging system

4. **Phase 4** - Plugin fallback updates (1 file)
   - Final cleanup for edge cases
   - Test: Run with logging accessor unavailable

---

## Expected Result

**Before**:
```
[PLUGIN]: Creating Python capsule for args
[PLUGIN]: Python capsule created successfully
[OPCUA INFO] OPC UA Plugin initializing...
{"timestamp": "1769785673", "level": "INFO", "message": "Logging initialized", "id": 8}
{"timestamp": "1769785673", "level": "INFO", "message": "Buffer accessor created", "id": 9}
[PLUGIN]: Skipping disabled plugin: s7comm
```

**After**:
```
[2024-01-30 12:00:00] [INFO] Creating Python capsule for args
[2024-01-30 12:00:00] [INFO] Python capsule created successfully
[2024-01-30 12:00:00] [INFO] OPC UA Plugin initializing...
[2024-01-30 12:00:00] [INFO] Logging initialized
[2024-01-30 12:00:00] [INFO] Buffer accessor created
[2024-01-30 12:00:00] [INFO] Skipping disabled plugin: s7comm
```

**API Response** (`/api/runtime-logs`) - unchanged, still JSON:
```json
{
  "runtime-logs": [
    {"id": 1, "timestamp": "2024-01-30T12:00:00+00:00", "level": "INFO", "message": "..."}
  ]
}
```

---

## Testing Checklist

- [ ] Phase 1: JSON logs now display as human-readable on stdout
- [ ] Phase 1: API `/api/runtime-logs` still returns JSON
- [ ] Phase 2: All C plugin messages go through logging system
- [ ] Phase 3: All Python print statements converted to logger
- [ ] Phase 4: Fallback logging matches standard format
- [ ] All timestamps are in UTC
- [ ] Log levels display correctly (INFO, WARN, ERROR, DEBUG)
- [ ] No duplicate messages (both printf and log_*)

---

## Status

- [x] Phase 1: Python Logging System (COMPLETED)
- [x] Phase 2: C printf/fprintf Conversions (COMPLETED)
- [x] Phase 3: Python print() Conversions (COMPLETED - webserver files)
- [x] Phase 4: Plugin Fallback Updates (COMPLETED)

### Note on Python Plugin Files
Python plugin files use `PluginLogger` from `shared/plugin_logger.py` or `OpcuaLogger`
from `opcua/opcua_logging.py`. Both have been updated with timestamp-formatted fallbacks.
