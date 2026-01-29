"""
IEC 61131-3 Type Converter

This module provides centralized type conversion functions for all IEC 61131-3 data types.
It is designed to be used by all plugins (OPC-UA, Modbus, S7Comm, EtherCAT, etc.) to eliminate
code duplication and ensure consistent type handling across the plugin ecosystem.

Main features:
- Value clamping with proper signed/unsigned handling
- Float <-> integer bit representation conversion
- Modbus register combination/splitting
- IEC_TIMESPEC handling for TIME/DATE/TOD/DT types
- Default value generation
- Endianness handling for multi-register types

Usage:
    from shared.iec_type_converter import IECTypeConverter

    # Clamp value to type bounds
    value = IECTypeConverter.clamp_to_type(1000, "SINT")  # Returns 127

    # Convert float to integer representation
    int_repr = IECTypeConverter.float_to_int_repr(3.14, "REAL")

    # Combine Modbus registers into value
    value = IECTypeConverter.registers_to_value([0x1234, 0x5678], "DINT")
"""

import ctypes
import struct
from datetime import datetime, timezone
from typing import Any, List, Tuple, Union

try:
    from .iec_type_registry import (
        FLOAT_TYPES,
        get_canonical_name,
        get_type_info,
    )
except ImportError:
    from iec_type_registry import (
        FLOAT_TYPES,
        get_canonical_name,
        get_type_info,
    )


class IECTypeConverter:
    """
    Centralized type conversion utilities for IEC 61131-3 data types.

    All methods are static and stateless, making this class thread-safe.
    """

    # -------------------------------------------------------------------------
    # Value Clamping and Type Coercion
    # -------------------------------------------------------------------------

    @staticmethod
    def clamp_to_type(value: Union[int, float], type_name: str) -> int:
        """
        Clamp a value to the bounds of an IEC type and return a ctypes-compatible value.

        This handles signed/unsigned conversion properly, ensuring that values
        outside the valid range are clamped and the result has the correct
        bit representation.

        Args:
            value: Value to clamp (will be converted to int for integer types)
            type_name: IEC type name (e.g., "SINT", "INT", "DINT")

        Returns:
            Clamped value with correct ctypes representation

        Raises:
            ValueError: If type_name is not a valid IEC type
        """
        info = get_type_info(type_name)
        if info is None:
            raise ValueError(f"Unknown IEC type: {type_name}")

        # Handle float types separately
        if info.is_float:
            return float(value)

        # Handle string type
        if info.is_string:
            return str(value)

        # Convert to int and clamp
        int_value = int(value)
        clamped = max(info.min_value, min(info.max_value, int_value))

        # Apply ctypes conversion to get correct bit representation
        return info.ctype_class(clamped).value

    @staticmethod
    def coerce_to_type(value: Any, type_name: str) -> Any:
        """
        Coerce a value to the appropriate Python type for an IEC type.

        This is a more permissive version of clamp_to_type that handles
        various input types (bool, str, etc.).

        Args:
            value: Value to coerce
            type_name: IEC type name

        Returns:
            Coerced value appropriate for the type
        """
        canonical = get_canonical_name(type_name)
        if canonical is None:
            return value

        info = get_type_info(canonical)
        if info is None:
            return value

        # Handle BOOL specially
        if canonical == "BOOL":
            if isinstance(value, bool):
                return 1 if value else 0
            elif isinstance(value, (int, float)):
                return 1 if value != 0 else 0
            elif isinstance(value, str):
                return 1 if value.lower() in ["true", "1", "yes", "on"] else 0
            else:
                return 1 if bool(value) else 0

        # Handle STRING
        if info.is_string:
            return str(value)

        # Handle floats
        if info.is_float:
            return float(value)

        # Handle time types (expect tuple or pass through)
        if info.is_time:
            if isinstance(value, tuple) and len(value) == 2:
                return value
            elif isinstance(value, datetime):
                return IECTypeConverter.datetime_to_timespec(value, canonical)
            return value

        # Handle integers
        return IECTypeConverter.clamp_to_type(value, canonical)

    # -------------------------------------------------------------------------
    # Float <-> Integer Bit Representation
    # -------------------------------------------------------------------------

    @staticmethod
    def float_to_int_repr(value: float, type_name: str) -> int:
        """
        Convert a float to its integer bit representation.

        This is used when floats need to be stored in integer buffers,
        preserving the exact bit pattern.

        Args:
            value: Float value to convert
            type_name: "REAL" (32-bit) or "LREAL" (64-bit)

        Returns:
            Integer with the same bit pattern as the float

        Raises:
            ValueError: If type_name is not REAL or LREAL
        """
        canonical = get_canonical_name(type_name)
        if canonical not in FLOAT_TYPES:
            raise ValueError(f"Type {type_name} is not a float type (REAL/LREAL)")

        try:
            if canonical == "REAL":
                # 32-bit float: pack as float, unpack as unsigned int
                return struct.unpack("I", struct.pack("f", float(value)))[0]
            else:  # LREAL
                # 64-bit double: pack as double, unpack as unsigned long long
                return struct.unpack("Q", struct.pack("d", float(value)))[0]
        except struct.error:
            # Fallback for extreme values
            return int(value)

    @staticmethod
    def int_repr_to_float(value: int, type_name: str) -> float:
        """
        Convert an integer bit representation back to a float.

        This reverses the operation of float_to_int_repr().

        Args:
            value: Integer with float bit pattern
            type_name: "REAL" (32-bit) or "LREAL" (64-bit)

        Returns:
            Float value

        Raises:
            ValueError: If type_name is not REAL or LREAL
        """
        canonical = get_canonical_name(type_name)
        if canonical not in FLOAT_TYPES:
            raise ValueError(f"Type {type_name} is not a float type (REAL/LREAL)")

        try:
            if canonical == "REAL":
                # 32-bit: unpack unsigned int as float
                return struct.unpack("f", struct.pack("I", value))[0]
            else:  # LREAL
                # 64-bit: unpack unsigned long long as double
                return struct.unpack("d", struct.pack("Q", value))[0]
        except struct.error:
            # Fallback
            return float(value)

    # -------------------------------------------------------------------------
    # Modbus Register Conversion
    # -------------------------------------------------------------------------

    @staticmethod
    def registers_to_value(registers: List[int], type_name: str, big_endian: bool = False) -> int:
        """
        Combine Modbus 16-bit registers into a single IEC value.

        Args:
            registers: List of 16-bit register values
            type_name: IEC type name (or size code: 'B', 'W', 'D', 'L')
            big_endian: If True, use big-endian byte order

        Returns:
            Combined value

        Raises:
            ValueError: If insufficient registers or invalid type
        """
        # Handle size codes for backward compatibility
        size_code_map = {"B": "BYTE", "W": "WORD", "D": "DWORD", "L": "LWORD", "X": "BOOL"}
        if type_name.upper() in size_code_map:
            type_name = size_code_map[type_name.upper()]

        info = get_type_info(type_name)
        if info is None:
            raise ValueError(f"Unknown IEC type: {type_name}")

        # BOOL is handled separately (coils, not registers)
        if info.register_count == 0:
            raise ValueError(f"Type {type_name} does not use registers")

        if len(registers) < info.register_count:
            raise ValueError(
                f"Need at least {info.register_count} registers for {type_name}, "
                f"got {len(registers)}"
            )

        if info.register_count == 1:
            # 8-bit or 16-bit: single register
            if info.size_bytes == 1:
                return registers[0] & 0xFF
            else:
                return registers[0] & 0xFFFF

        elif info.register_count == 2:
            # 32-bit: 2 registers
            if big_endian:
                value = (registers[0] << 16) | registers[1]
            else:
                value = (registers[1] << 16) | registers[0]

            # Apply sign conversion if needed
            if info.signed and not info.is_float:
                return ctypes.c_int32(value).value
            return value

        elif info.register_count == 4:
            # 64-bit: 4 registers
            if big_endian:
                value = (
                    (registers[0] << 48)
                    | (registers[1] << 32)
                    | (registers[2] << 16)
                    | registers[3]
                )
            else:
                value = (
                    (registers[3] << 48)
                    | (registers[2] << 32)
                    | (registers[1] << 16)
                    | registers[0]
                )

            # Apply sign conversion if needed
            if info.signed and not info.is_float:
                return ctypes.c_int64(value).value
            return value

        else:
            raise ValueError(f"Unsupported register count: {info.register_count}")

    @staticmethod
    def value_to_registers(value: int, type_name: str, big_endian: bool = False) -> List[int]:
        """
        Split an IEC value into Modbus 16-bit registers.

        Args:
            value: IEC value to split
            type_name: IEC type name (or size code: 'B', 'W', 'D', 'L')
            big_endian: If True, use big-endian byte order

        Returns:
            List of 16-bit register values

        Raises:
            ValueError: If invalid type
        """
        # Handle size codes for backward compatibility
        size_code_map = {"B": "BYTE", "W": "WORD", "D": "DWORD", "L": "LWORD", "X": "BOOL"}
        if type_name.upper() in size_code_map:
            type_name = size_code_map[type_name.upper()]

        info = get_type_info(type_name)
        if info is None:
            raise ValueError(f"Unknown IEC type: {type_name}")

        if info.register_count == 0:
            raise ValueError(f"Type {type_name} does not use registers")

        # Convert to unsigned for bit manipulation
        if info.signed and value < 0:
            if info.size_bytes == 4:
                value = ctypes.c_uint32(value).value
            elif info.size_bytes == 8:
                value = ctypes.c_uint64(value).value
            elif info.size_bytes == 2:
                value = ctypes.c_uint16(value).value
            elif info.size_bytes == 1:
                value = ctypes.c_uint8(value).value

        if info.register_count == 1:
            # 8-bit or 16-bit
            if info.size_bytes == 1:
                return [value & 0xFF]
            else:
                return [value & 0xFFFF]

        elif info.register_count == 2:
            # 32-bit
            if big_endian:
                return [(value >> 16) & 0xFFFF, value & 0xFFFF]
            else:
                return [value & 0xFFFF, (value >> 16) & 0xFFFF]

        elif info.register_count == 4:
            # 64-bit
            if big_endian:
                return [
                    (value >> 48) & 0xFFFF,
                    (value >> 32) & 0xFFFF,
                    (value >> 16) & 0xFFFF,
                    value & 0xFFFF,
                ]
            else:
                return [
                    value & 0xFFFF,
                    (value >> 16) & 0xFFFF,
                    (value >> 32) & 0xFFFF,
                    (value >> 48) & 0xFFFF,
                ]

        else:
            raise ValueError(f"Unsupported register count: {info.register_count}")

    @staticmethod
    def get_register_count(type_name: str) -> int:
        """
        Get the number of 16-bit Modbus registers needed for a type.

        Args:
            type_name: IEC type name or size code

        Returns:
            Number of registers (0 for BOOL, 1 for 8/16-bit, 2 for 32-bit, 4 for 64-bit)
        """
        # Handle size codes
        size_code_map = {"X": 0, "B": 1, "W": 1, "D": 2, "L": 4}
        if type_name.upper() in size_code_map:
            return size_code_map[type_name.upper()]

        info = get_type_info(type_name)
        if info:
            return info.register_count
        return 1  # Default fallback

    # -------------------------------------------------------------------------
    # Time Type Conversions (IEC_TIMESPEC)
    # -------------------------------------------------------------------------

    @staticmethod
    def timespec_to_milliseconds(tv_sec: int, tv_nsec: int) -> int:
        """
        Convert IEC_TIMESPEC (tv_sec, tv_nsec) to milliseconds.

        Args:
            tv_sec: Seconds component
            tv_nsec: Nanoseconds component

        Returns:
            Total time in milliseconds
        """
        return (tv_sec * 1000) + (tv_nsec // 1_000_000)

    @staticmethod
    def milliseconds_to_timespec(ms: int) -> Tuple[int, int]:
        """
        Convert milliseconds to IEC_TIMESPEC format.

        Args:
            ms: Time in milliseconds

        Returns:
            Tuple of (tv_sec, tv_nsec)
        """
        tv_sec = ms // 1000
        tv_nsec = (ms % 1000) * 1_000_000
        return (tv_sec, tv_nsec)

    @staticmethod
    def timespec_to_datetime(tv_sec: int, tv_nsec: int, time_type: str) -> datetime:
        """
        Convert IEC_TIMESPEC to Python datetime.

        Args:
            tv_sec: Seconds component
            tv_nsec: Nanoseconds component
            time_type: One of "TIME", "DATE", "TOD", "DT"

        Returns:
            datetime object (UTC)
        """
        canonical = get_canonical_name(time_type)

        if canonical == "TIME":
            # TIME is a duration, not a point in time
            # Return as datetime offset from epoch
            try:
                return datetime.fromtimestamp(tv_sec, tz=timezone.utc).replace(
                    microsecond=tv_nsec // 1000
                )
            except (OSError, OverflowError, ValueError):
                return datetime(1970, 1, 1, tzinfo=timezone.utc)

        elif canonical == "TOD":
            # TOD: seconds since midnight
            hours = tv_sec // 3600
            minutes = (tv_sec % 3600) // 60
            seconds = tv_sec % 60
            microseconds = tv_nsec // 1000

            today = datetime.now(timezone.utc).date()
            try:
                return datetime(
                    today.year,
                    today.month,
                    today.day,
                    hours % 24,  # Clamp to valid range
                    minutes % 60,
                    seconds % 60,
                    microseconds % 1_000_000,
                    tzinfo=timezone.utc,
                )
            except (ValueError, OverflowError):
                return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

        elif canonical == "DATE":
            # DATE: seconds since epoch, time portion ignored
            try:
                dt = datetime.fromtimestamp(tv_sec, tz=timezone.utc)
                return dt.replace(hour=0, minute=0, second=0, microsecond=0)
            except (OSError, OverflowError, ValueError):
                return datetime(1970, 1, 1, tzinfo=timezone.utc)

        elif canonical == "DT":
            # DT: full date and time from epoch
            try:
                dt = datetime.fromtimestamp(tv_sec, tz=timezone.utc)
                return dt.replace(microsecond=tv_nsec // 1000)
            except (OSError, OverflowError, ValueError):
                return datetime(1970, 1, 1, tzinfo=timezone.utc)

        else:
            # Unknown time type, treat as epoch timestamp
            try:
                return datetime.fromtimestamp(tv_sec, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return datetime(1970, 1, 1, tzinfo=timezone.utc)

    @staticmethod
    def datetime_to_timespec(dt: datetime, time_type: str) -> Tuple[int, int]:
        """
        Convert Python datetime to IEC_TIMESPEC.

        Args:
            dt: datetime object
            time_type: One of "TIME", "DATE", "TOD", "DT"

        Returns:
            Tuple of (tv_sec, tv_nsec)
        """
        canonical = get_canonical_name(time_type)

        if canonical == "TOD":
            # TOD: extract time portion only (seconds since midnight)
            tv_sec = dt.hour * 3600 + dt.minute * 60 + dt.second
            tv_nsec = dt.microsecond * 1000
            return (tv_sec, tv_nsec)

        elif canonical == "DATE":
            # DATE: midnight of the date
            dt_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            tv_sec = int(dt_midnight.timestamp())
            return (tv_sec, 0)

        else:
            # TIME, DT: full timestamp
            tv_sec = int(dt.timestamp())
            tv_nsec = dt.microsecond * 1000
            return (tv_sec, tv_nsec)

    # -------------------------------------------------------------------------
    # Default Values
    # -------------------------------------------------------------------------

    @staticmethod
    def get_default_value(type_name: str) -> Any:
        """
        Get the default/safe value for an IEC type.

        Args:
            type_name: IEC type name

        Returns:
            Appropriate default value for the type
        """
        canonical = get_canonical_name(type_name)
        if canonical is None:
            return 0

        info = get_type_info(canonical)
        if info is None:
            return 0

        if canonical == "BOOL":
            return False
        elif info.is_float:
            return 0.0
        elif info.is_string:
            return ""
        elif info.is_time:
            return (0, 0)  # IEC_TIMESPEC
        else:
            return 0

    @staticmethod
    def get_default_value_for_protocol(type_name: str, protocol: str) -> Any:
        """
        Get the default value formatted for a specific protocol.

        Args:
            type_name: IEC type name
            protocol: Protocol name ("opcua", "modbus", "s7comm")

        Returns:
            Protocol-appropriate default value
        """
        canonical = get_canonical_name(type_name)
        if canonical is None:
            return 0

        info = get_type_info(canonical)
        if info is None:
            return 0

        if protocol.lower() == "opcua":
            if canonical == "BOOL":
                return False
            elif info.is_float:
                return 0.0
            elif info.is_string:
                return ""
            elif canonical == "TIME":
                return 0  # Milliseconds for OPC-UA
            elif canonical in ("DATE", "TOD", "DT"):
                return datetime(1970, 1, 1, tzinfo=timezone.utc)
            else:
                return 0

        elif protocol.lower() == "modbus":
            # Modbus always uses integers/register values
            if canonical == "BOOL":
                return 0
            else:
                return 0

        else:
            return IECTypeConverter.get_default_value(type_name)

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    @staticmethod
    def is_type_signed(type_name: str) -> bool:
        """
        Check if an IEC type is signed.

        Args:
            type_name: IEC type name

        Returns:
            True if signed, False otherwise
        """
        info = get_type_info(type_name)
        return info.signed if info else False

    @staticmethod
    def get_size_bytes(type_name: str) -> int:
        """
        Get the size in bytes for an IEC type.

        Args:
            type_name: IEC type name

        Returns:
            Size in bytes, or 0 if type not found
        """
        info = get_type_info(type_name)
        return info.size_bytes if info else 0

    @staticmethod
    def swap_endianness_16(value: int) -> int:
        """Swap bytes in a 16-bit value."""
        return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

    @staticmethod
    def swap_endianness_32(value: int) -> int:
        """Swap bytes in a 32-bit value."""
        return (
            ((value & 0x000000FF) << 24)
            | ((value & 0x0000FF00) << 8)
            | ((value & 0x00FF0000) >> 8)
            | ((value & 0xFF000000) >> 24)
        )

    @staticmethod
    def swap_endianness_64(value: int) -> int:
        """Swap bytes in a 64-bit value."""
        return (
            ((value & 0x00000000000000FF) << 56)
            | ((value & 0x000000000000FF00) << 40)
            | ((value & 0x0000000000FF0000) << 24)
            | ((value & 0x00000000FF000000) << 8)
            | ((value & 0x000000FF00000000) >> 8)
            | ((value & 0x0000FF0000000000) >> 24)
            | ((value & 0x00FF000000000000) >> 40)
            | ((value & 0xFF00000000000000) >> 56)
        )

    @staticmethod
    def swap_endianness(value: int, type_name: str) -> int:
        """
        Swap endianness of a value based on its type size.

        Args:
            value: Value to swap
            type_name: IEC type name

        Returns:
            Value with swapped endianness
        """
        info = get_type_info(type_name)
        if info is None:
            return value

        if info.size_bytes == 2:
            return IECTypeConverter.swap_endianness_16(value)
        elif info.size_bytes == 4:
            return IECTypeConverter.swap_endianness_32(value)
        elif info.size_bytes == 8:
            return IECTypeConverter.swap_endianness_64(value)
        else:
            return value
