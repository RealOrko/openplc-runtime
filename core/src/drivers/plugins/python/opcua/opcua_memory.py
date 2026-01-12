"""OPC-UA plugin memory access utilities."""

import ctypes
import os
import sys
from typing import Any, List, Dict

# Add directories to path for module access
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

# Import local modules (handle both package and direct loading)
try:
    from .opcua_types import VariableMetadata
    from .opcua_logging import log_info, log_warn, log_error
except ImportError:
    from opcua_types import VariableMetadata
    from opcua_logging import log_info, log_warn, log_error


# IEC 61131-3 STRING constants (must match iec_types.h)
STR_MAX_LEN = 126
STR_LEN_SIZE = 1  # sizeof(__strlen_t) = sizeof(int8_t) = 1
STRING_TOTAL_SIZE = STR_LEN_SIZE + STR_MAX_LEN  # 127 bytes


class IEC_STRING(ctypes.Structure):
    """
    ctypes structure matching IEC_STRING from iec_types.h.

    typedef struct {
        __strlen_t len;        // int8_t, 1 byte
        uint8_t body[126];     // 126 bytes
    } IEC_STRING;
    """
    _fields_ = [
        ("len", ctypes.c_int8),
        ("body", ctypes.c_uint8 * STR_MAX_LEN),
    ]


def read_memory_direct(address: int, size: int) -> Any:
    """
    Read value directly from memory using cached address.

    Args:
        address: Memory address to read from
        size: Size of the variable in bytes

    Returns:
        Value read from memory (int for numeric types, str for STRING)

    Raises:
        RuntimeError: If memory access fails
        ValueError: If size is not supported
    """
    try:
        if size == 1:
            ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint8))
            return ptr.contents.value
        elif size == 2:
            ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint16))
            return ptr.contents.value
        elif size == 4:
            ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32))
            return ptr.contents.value
        elif size == 8:
            ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint64))
            return ptr.contents.value
        elif size == STRING_TOTAL_SIZE:
            # STRING type: read IEC_STRING structure and decode to Python string
            return read_string_direct(address)
        else:
            raise ValueError(f"Unsupported variable size: {size}")
    except Exception as e:
        raise RuntimeError(f"Memory access error: {e}")


def read_string_direct(address: int) -> str:
    """
    Read an IEC_STRING directly from memory.

    Args:
        address: Memory address of the IEC_STRING structure

    Returns:
        Python string decoded from the IEC_STRING
    """
    try:
        ptr = ctypes.cast(address, ctypes.POINTER(IEC_STRING))
        iec_string = ptr.contents

        # Get the actual length (clamped to valid range)
        str_len = max(0, min(iec_string.len, STR_MAX_LEN))

        if str_len == 0:
            return ""

        # Extract bytes from body array and decode
        raw_bytes = bytes(iec_string.body[:str_len])
        return raw_bytes.decode('utf-8', errors='replace')

    except Exception as e:
        raise RuntimeError(f"String memory access error: {e}")


def write_string_direct(address: int, value: str) -> bool:
    """
    Write a Python string to an IEC_STRING in memory.

    Args:
        address: Memory address of the IEC_STRING structure
        value: Python string to write

    Returns:
        True if successful
    """
    try:
        ptr = ctypes.cast(address, ctypes.POINTER(IEC_STRING))
        iec_string = ptr.contents

        # Encode string to bytes and truncate if necessary
        encoded = value.encode('utf-8', errors='replace')
        str_len = min(len(encoded), STR_MAX_LEN)

        # Set length
        iec_string.len = str_len

        # Copy bytes to body
        for i in range(str_len):
            iec_string.body[i] = encoded[i]

        # Zero-fill remainder (optional, for cleanliness)
        for i in range(str_len, STR_MAX_LEN):
            iec_string.body[i] = 0

        return True

    except Exception as e:
        raise RuntimeError(f"String memory write error: {e}")


def initialize_variable_cache(sba, indices: List[int]) -> Dict[int, VariableMetadata]:
    """Initialize metadata cache for direct memory access."""
    try:
        # Try relative imports first (when used as package)
        from .opcua_utils import infer_var_type
    except ImportError:
        # Fallback to absolute imports (when run standalone)
        from opcua_utils import infer_var_type

    try:
        # Batch: get addresses
        addresses, addr_msg = sba.get_var_list(indices)
        if addr_msg != "Success":
            log_warn(f"Failed to cache addresses: {addr_msg}")
            return {}

        # Batch: get sizes
        sizes, size_msg = sba.get_var_sizes_batch(indices)
        if size_msg != "Success":
            log_warn(f"Failed to cache sizes: {size_msg}")
            return {}

        # Create cache
        cache = {}
        for i, var_index in enumerate(indices):
            if addresses[i] is not None and sizes[i] > 0:
                metadata = VariableMetadata(
                    index=var_index,
                    address=addresses[i],
                    size=sizes[i],
                    inferred_type=infer_var_type(sizes[i])
                )
                cache[var_index] = metadata

        log_info(f"Cached metadata for {len(cache)} variables")
        return cache

    except Exception as e:
        log_warn(f"Failed to initialize variable cache: {e}")
        return {}
