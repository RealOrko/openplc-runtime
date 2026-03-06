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
    from .opcua_logging import log_debug, log_error, log_info, log_warn
except ImportError:
    from opcua_types import VariableMetadata
    from opcua_logging import log_debug, log_error, log_info, log_warn


# IEC 61131-3 STRING constants (must match iec_types.h)
STR_MAX_LEN = 126
STR_LEN_SIZE = 1  # sizeof(__strlen_t) = sizeof(int8_t) = 1
STRING_TOTAL_SIZE = STR_LEN_SIZE + STR_MAX_LEN  # 127 bytes

# IEC 61131-3 TIME/DATE constants (must match iec_types.h)
TIMESPEC_SIZE = 8  # sizeof(IEC_TIMESPEC) = 2 * sizeof(int32_t) = 8 bytes

# TIME-related datatypes that use IEC_TIMESPEC structure
TIME_DATATYPES = frozenset(["TIME", "DATE", "TOD", "DT"])


def _validate_memory_address(address: int, size: int = 1) -> None:
    """
    Validate a memory address before access.

    Args:
        address: Memory address to validate
        size: Size of data to be accessed (for bounds context)

    Raises:
        ValueError: If address is invalid (NULL, negative, or suspiciously small)
    """
    if address is None:
        raise ValueError("Memory address is None")
    if not isinstance(address, int):
        raise ValueError(f"Memory address must be an integer, got {type(address).__name__}")
    if address == 0:
        raise ValueError("Memory address is NULL (0)")
    if address < 0:
        raise ValueError(f"Memory address is negative: {address}")
    # Addresses below 4096 are typically reserved/unmapped on most systems
    if address < 4096:
        raise ValueError(f"Memory address {address} is in reserved memory region (< 4096)")


class IEC_TIMESPEC(ctypes.Structure):
    """
    ctypes structure matching IEC_TIMESPEC from iec_types.h.

    typedef struct {
        int32_t tv_sec;   // Seconds
        int32_t tv_nsec;  // Nanoseconds
    } IEC_TIMESPEC;

    Used for TIME, DATE, TOD, and DT types.
    """

    _fields_ = [
        ("tv_sec", ctypes.c_int32),
        ("tv_nsec", ctypes.c_int32),
    ]


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


def read_memory_direct(address: int, size: int, datatype: str = None) -> Any:
    """
    Read value directly from memory using cached address.

    Args:
        address: Memory address to read from
        size: Size of the variable in bytes
        datatype: Optional datatype hint for ambiguous sizes (e.g., TIME vs LINT)

    Returns:
        Value read from memory:
        - int for numeric types
        - str for STRING
        - tuple(tv_sec, tv_nsec) for TIME/DATE/TOD/DT

    Raises:
        RuntimeError: If memory access fails
        ValueError: If size is not supported or address is invalid
    """
    # Validate address before any memory access
    _validate_memory_address(address, size)

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
            # Check if this is a TIME-related type
            if datatype and datatype.upper() in TIME_DATATYPES:
                return read_timespec_direct(address)
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

    Raises:
        ValueError: If address is invalid
        RuntimeError: If memory access fails
    """
    _validate_memory_address(address, STRING_TOTAL_SIZE)

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

    Raises:
        ValueError: If address is invalid
        RuntimeError: If memory access fails
    """
    _validate_memory_address(address, STRING_TOTAL_SIZE)

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


def read_timespec_direct(address: int) -> tuple[int, int]:
    """
    Read an IEC_TIMESPEC directly from memory.

    Args:
        address: Memory address of the IEC_TIMESPEC structure

    Returns:
        Tuple of (tv_sec, tv_nsec)

    Raises:
        ValueError: If address is invalid
        RuntimeError: If memory access fails
    """
    _validate_memory_address(address, TIMESPEC_SIZE)

    try:
        ptr = ctypes.cast(address, ctypes.POINTER(IEC_TIMESPEC))
        timespec = ptr.contents
        return (timespec.tv_sec, timespec.tv_nsec)
    except Exception as e:
        raise RuntimeError(f"Timespec memory access error: {e}")


def write_timespec_direct(address: int, tv_sec: int, tv_nsec: int) -> bool:
    """
    Write an IEC_TIMESPEC to memory.

    Args:
        address: Memory address of the IEC_TIMESPEC structure
        tv_sec: Seconds value (int32)
        tv_nsec: Nanoseconds value (int32)

    Returns:
        True if successful

    Raises:
        ValueError: If address is invalid
        RuntimeError: If memory access fails
    """
    _validate_memory_address(address, TIMESPEC_SIZE)

    try:
        ptr = ctypes.cast(address, ctypes.POINTER(IEC_TIMESPEC))
        ptr.contents.tv_sec = ctypes.c_int32(tv_sec).value
        ptr.contents.tv_nsec = ctypes.c_int32(tv_nsec).value
        return True
    except Exception as e:
        raise RuntimeError(f"Timespec memory write error: {e}")


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

        log_debug(f"Cached metadata for {len(cache)} variables")
        return cache

    except Exception as e:
        log_warn(f"Failed to initialize variable cache: {e}")
        return {}
