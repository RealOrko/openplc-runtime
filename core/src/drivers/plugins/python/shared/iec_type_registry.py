"""
IEC 61131-3 Type Registry

This module provides a centralized registry of all IEC 61131-3 data types with their
metadata. It serves as the single source of truth for type information used by all
plugins (OPC-UA, Modbus, S7Comm, EtherCAT, etc.).

The registry eliminates the need for each plugin to maintain its own type definitions,
ensuring consistency across the entire plugin ecosystem.

Supported types (22 total):
- Boolean: BOOL
- Integer 8-bit: SINT, USINT, BYTE
- Integer 16-bit: INT, UINT, WORD
- Integer 32-bit: DINT, UDINT, DWORD
- Integer 64-bit: LINT, ULINT, LWORD
- Floating point: REAL, LREAL
- Time types: TIME, DATE, TOD, DT
- String: STRING
"""

import ctypes
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple, Type


@dataclass(frozen=True)
class IECTypeInfo:
    """
    Immutable metadata for an IEC 61131-3 data type.

    Attributes:
        name: Canonical type name (e.g., "SINT", "INT", "DINT")
        size_bits: Size in bits (1, 8, 16, 32, 64)
        size_bytes: Size in bytes (1, 2, 4, 8)
        signed: True for signed types (SINT, INT, DINT, LINT)
        min_value: Minimum valid value
        max_value: Maximum valid value
        ctype_class: Corresponding ctypes class for memory operations
        is_float: True for REAL and LREAL
        is_time: True for TIME, DATE, TOD, DT
        is_string: True for STRING
        register_count: Number of 16-bit Modbus registers needed (0 for BOOL)
        iec_size_code: IEC size code ('X'=bit, 'B'=byte, 'W'=word, 'D'=dword, 'L'=lword)
        aliases: Tuple of alternative names for this type
    """

    name: str
    size_bits: int
    size_bytes: int
    signed: bool
    min_value: int
    max_value: int
    ctype_class: Type
    is_float: bool
    is_time: bool
    is_string: bool
    register_count: int
    iec_size_code: str
    aliases: Tuple[str, ...]


# Type bounds constants for clarity
_SINT_MIN, _SINT_MAX = -128, 127
_USINT_MIN, _USINT_MAX = 0, 255
_INT_MIN, _INT_MAX = -32768, 32767
_UINT_MIN, _UINT_MAX = 0, 65535
_DINT_MIN, _DINT_MAX = -2147483648, 2147483647
_UDINT_MIN, _UDINT_MAX = 0, 4294967295
_LINT_MIN, _LINT_MAX = -9223372036854775808, 9223372036854775807
_ULINT_MIN, _ULINT_MAX = 0, 18446744073709551615


# Complete IEC 61131-3 Type Registry
_IEC_TYPE_REGISTRY: Dict[str, IECTypeInfo] = {
    # Boolean type
    "BOOL": IECTypeInfo(
        name="BOOL",
        size_bits=1,
        size_bytes=1,
        signed=False,
        min_value=0,
        max_value=1,
        ctype_class=ctypes.c_uint8,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=0,  # Handled via coils, not registers
        iec_size_code="X",
        aliases=(),
    ),
    # 8-bit signed integer
    "SINT": IECTypeInfo(
        name="SINT",
        size_bits=8,
        size_bytes=1,
        signed=True,
        min_value=_SINT_MIN,
        max_value=_SINT_MAX,
        ctype_class=ctypes.c_int8,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=1,
        iec_size_code="B",
        aliases=("INT8",),
    ),
    # 8-bit unsigned integer
    "USINT": IECTypeInfo(
        name="USINT",
        size_bits=8,
        size_bytes=1,
        signed=False,
        min_value=_USINT_MIN,
        max_value=_USINT_MAX,
        ctype_class=ctypes.c_uint8,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=1,
        iec_size_code="B",
        aliases=("UINT8",),
    ),
    # 8-bit unsigned (bit string semantics)
    "BYTE": IECTypeInfo(
        name="BYTE",
        size_bits=8,
        size_bytes=1,
        signed=False,
        min_value=_USINT_MIN,
        max_value=_USINT_MAX,
        ctype_class=ctypes.c_uint8,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=1,
        iec_size_code="B",
        aliases=(),
    ),
    # 16-bit signed integer
    "INT": IECTypeInfo(
        name="INT",
        size_bits=16,
        size_bytes=2,
        signed=True,
        min_value=_INT_MIN,
        max_value=_INT_MAX,
        ctype_class=ctypes.c_int16,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=1,
        iec_size_code="W",
        aliases=("INT16",),
    ),
    # 16-bit unsigned integer
    "UINT": IECTypeInfo(
        name="UINT",
        size_bits=16,
        size_bytes=2,
        signed=False,
        min_value=_UINT_MIN,
        max_value=_UINT_MAX,
        ctype_class=ctypes.c_uint16,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=1,
        iec_size_code="W",
        aliases=("UINT16",),
    ),
    # 16-bit unsigned (bit string semantics)
    "WORD": IECTypeInfo(
        name="WORD",
        size_bits=16,
        size_bytes=2,
        signed=False,
        min_value=_UINT_MIN,
        max_value=_UINT_MAX,
        ctype_class=ctypes.c_uint16,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=1,
        iec_size_code="W",
        aliases=(),
    ),
    # 32-bit signed integer
    "DINT": IECTypeInfo(
        name="DINT",
        size_bits=32,
        size_bytes=4,
        signed=True,
        min_value=_DINT_MIN,
        max_value=_DINT_MAX,
        ctype_class=ctypes.c_int32,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=2,
        iec_size_code="D",
        aliases=("INT32",),
    ),
    # 32-bit unsigned integer
    "UDINT": IECTypeInfo(
        name="UDINT",
        size_bits=32,
        size_bytes=4,
        signed=False,
        min_value=_UDINT_MIN,
        max_value=_UDINT_MAX,
        ctype_class=ctypes.c_uint32,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=2,
        iec_size_code="D",
        aliases=("UINT32",),
    ),
    # 32-bit unsigned (bit string semantics)
    "DWORD": IECTypeInfo(
        name="DWORD",
        size_bits=32,
        size_bytes=4,
        signed=False,
        min_value=_UDINT_MIN,
        max_value=_UDINT_MAX,
        ctype_class=ctypes.c_uint32,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=2,
        iec_size_code="D",
        aliases=(),
    ),
    # 64-bit signed integer
    "LINT": IECTypeInfo(
        name="LINT",
        size_bits=64,
        size_bytes=8,
        signed=True,
        min_value=_LINT_MIN,
        max_value=_LINT_MAX,
        ctype_class=ctypes.c_int64,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=("INT64",),
    ),
    # 64-bit unsigned integer
    "ULINT": IECTypeInfo(
        name="ULINT",
        size_bits=64,
        size_bytes=8,
        signed=False,
        min_value=_ULINT_MIN,
        max_value=_ULINT_MAX,
        ctype_class=ctypes.c_uint64,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=("UINT64",),
    ),
    # 64-bit unsigned (bit string semantics)
    "LWORD": IECTypeInfo(
        name="LWORD",
        size_bits=64,
        size_bytes=8,
        signed=False,
        min_value=_ULINT_MIN,
        max_value=_ULINT_MAX,
        ctype_class=ctypes.c_uint64,
        is_float=False,
        is_time=False,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=(),
    ),
    # 32-bit floating point
    "REAL": IECTypeInfo(
        name="REAL",
        size_bits=32,
        size_bytes=4,
        signed=True,  # Floats are inherently signed
        min_value=_DINT_MIN,  # Integer representation bounds
        max_value=_DINT_MAX,
        ctype_class=ctypes.c_float,
        is_float=True,
        is_time=False,
        is_string=False,
        register_count=2,
        iec_size_code="D",
        aliases=("FLOAT",),
    ),
    # 64-bit floating point
    "LREAL": IECTypeInfo(
        name="LREAL",
        size_bits=64,
        size_bytes=8,
        signed=True,  # Floats are inherently signed
        min_value=_LINT_MIN,  # Integer representation bounds
        max_value=_LINT_MAX,
        ctype_class=ctypes.c_double,
        is_float=True,
        is_time=False,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=("DOUBLE",),
    ),
    # Time duration (stored as IEC_TIMESPEC: tv_sec + tv_nsec)
    "TIME": IECTypeInfo(
        name="TIME",
        size_bits=64,
        size_bytes=8,
        signed=True,
        min_value=_LINT_MIN,
        max_value=_LINT_MAX,
        ctype_class=ctypes.c_int64,
        is_float=False,
        is_time=True,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=(),
    ),
    # Date (stored as IEC_TIMESPEC: seconds since epoch)
    "DATE": IECTypeInfo(
        name="DATE",
        size_bits=64,
        size_bytes=8,
        signed=True,
        min_value=_LINT_MIN,
        max_value=_LINT_MAX,
        ctype_class=ctypes.c_int64,
        is_float=False,
        is_time=True,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=("D",),
    ),
    # Time of day (stored as IEC_TIMESPEC: seconds since midnight)
    "TOD": IECTypeInfo(
        name="TOD",
        size_bits=64,
        size_bytes=8,
        signed=True,
        min_value=_LINT_MIN,
        max_value=_LINT_MAX,
        ctype_class=ctypes.c_int64,
        is_float=False,
        is_time=True,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=("TIME_OF_DAY",),
    ),
    # Date and time (stored as IEC_TIMESPEC: seconds since epoch)
    "DT": IECTypeInfo(
        name="DT",
        size_bits=64,
        size_bytes=8,
        signed=True,
        min_value=_LINT_MIN,
        max_value=_LINT_MAX,
        ctype_class=ctypes.c_int64,
        is_float=False,
        is_time=True,
        is_string=False,
        register_count=4,
        iec_size_code="L",
        aliases=("DATE_AND_TIME",),
    ),
    # Variable-length string (IEC_STRING: 1 byte len + 126 bytes body)
    "STRING": IECTypeInfo(
        name="STRING",
        size_bits=127 * 8,
        size_bytes=127,
        signed=False,
        min_value=0,
        max_value=126,  # Max string length
        ctype_class=ctypes.c_char,
        is_float=False,
        is_time=False,
        is_string=True,
        register_count=64,  # 127 bytes = 64 registers (rounded up)
        iec_size_code="S",
        aliases=(),
    ),
}

# Build alias lookup table
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _type_name, _type_info in _IEC_TYPE_REGISTRY.items():
    for alias in _type_info.aliases:
        _ALIAS_TO_CANONICAL[alias.upper()] = _type_name

# Frozen sets for type categories
INTEGER_TYPES: FrozenSet[str] = frozenset(
    [
        "SINT",
        "USINT",
        "BYTE",
        "INT",
        "UINT",
        "WORD",
        "DINT",
        "UDINT",
        "DWORD",
        "LINT",
        "ULINT",
        "LWORD",
    ]
)

SIGNED_INTEGER_TYPES: FrozenSet[str] = frozenset(["SINT", "INT", "DINT", "LINT"])

UNSIGNED_INTEGER_TYPES: FrozenSet[str] = frozenset(
    ["USINT", "BYTE", "UINT", "WORD", "UDINT", "DWORD", "ULINT", "LWORD"]
)

FLOAT_TYPES: FrozenSet[str] = frozenset(["REAL", "LREAL"])

TIME_TYPES: FrozenSet[str] = frozenset(["TIME", "DATE", "TOD", "DT"])

BIT_STRING_TYPES: FrozenSet[str] = frozenset(["BYTE", "WORD", "DWORD", "LWORD"])

ALL_TYPES: FrozenSet[str] = frozenset(_IEC_TYPE_REGISTRY.keys())


def get_type_info(type_name: str) -> Optional[IECTypeInfo]:
    """
    Get type information for an IEC 61131-3 type.

    Args:
        type_name: Type name (case-insensitive), can be canonical or alias

    Returns:
        IECTypeInfo if found, None otherwise
    """
    upper_name = type_name.upper()

    # Try direct lookup first
    if upper_name in _IEC_TYPE_REGISTRY:
        return _IEC_TYPE_REGISTRY[upper_name]

    # Try alias lookup
    canonical = _ALIAS_TO_CANONICAL.get(upper_name)
    if canonical:
        return _IEC_TYPE_REGISTRY[canonical]

    return None


def get_canonical_name(type_name: str) -> Optional[str]:
    """
    Get the canonical name for a type (resolves aliases).

    Args:
        type_name: Type name (case-insensitive), can be canonical or alias

    Returns:
        Canonical type name if found, None otherwise
    """
    upper_name = type_name.upper()

    if upper_name in _IEC_TYPE_REGISTRY:
        return upper_name

    return _ALIAS_TO_CANONICAL.get(upper_name)


def is_valid_type(type_name: str) -> bool:
    """
    Check if a type name is valid (canonical or alias).

    Args:
        type_name: Type name to check (case-insensitive)

    Returns:
        True if valid, False otherwise
    """
    upper_name = type_name.upper()
    return upper_name in _IEC_TYPE_REGISTRY or upper_name in _ALIAS_TO_CANONICAL


def get_type_bounds(type_name: str) -> Optional[Tuple[int, int]]:
    """
    Get the value bounds for a type.

    Args:
        type_name: Type name (case-insensitive)

    Returns:
        Tuple of (min_value, max_value) if found, None otherwise
    """
    info = get_type_info(type_name)
    if info:
        return (info.min_value, info.max_value)
    return None


def get_register_count(type_name: str) -> int:
    """
    Get the number of 16-bit Modbus registers needed for a type.

    Args:
        type_name: Type name (case-insensitive)

    Returns:
        Number of registers, or 0 if type not found or is BOOL
    """
    info = get_type_info(type_name)
    if info:
        return info.register_count
    return 0


def get_size_code(type_name: str) -> Optional[str]:
    """
    Get the IEC size code for a type.

    Args:
        type_name: Type name (case-insensitive)

    Returns:
        Size code ('X', 'B', 'W', 'D', 'L', 'S') or None if not found
    """
    info = get_type_info(type_name)
    if info:
        return info.iec_size_code
    return None


def get_ctype_class(type_name: str) -> Optional[Type]:
    """
    Get the ctypes class for a type.

    Args:
        type_name: Type name (case-insensitive)

    Returns:
        ctypes class or None if not found
    """
    info = get_type_info(type_name)
    if info:
        return info.ctype_class
    return None


def get_all_types() -> Dict[str, IECTypeInfo]:
    """
    Get a copy of the complete type registry.

    Returns:
        Dictionary mapping type names to IECTypeInfo objects
    """
    return _IEC_TYPE_REGISTRY.copy()


def get_types_by_size(size_bytes: int) -> FrozenSet[str]:
    """
    Get all types with a specific size in bytes.

    Args:
        size_bytes: Size in bytes (1, 2, 4, 8, 127)

    Returns:
        Frozen set of type names with that size
    """
    return frozenset(
        name for name, info in _IEC_TYPE_REGISTRY.items() if info.size_bytes == size_bytes
    )


def size_code_to_type_name(size_code: str) -> Optional[str]:
    """
    Get a representative type name for an IEC size code.

    This is useful for Modbus operations that use size codes.

    Args:
        size_code: IEC size code ('X', 'B', 'W', 'D', 'L')

    Returns:
        Representative type name (unsigned variant) or None if invalid
    """
    mapping = {
        "X": "BOOL",
        "B": "BYTE",
        "W": "WORD",
        "D": "DWORD",
        "L": "LWORD",
    }
    return mapping.get(size_code.upper())
