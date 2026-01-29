"""
Unit tests for IEC 61131-3 Type Converter

Tests the iec_type_converter module which provides centralized type conversion
functions for all IEC 61131-3 data types.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add shared module to path
shared_path = Path(__file__).parent.parent.parent / "core/src/drivers/plugins/python/shared"
sys.path.insert(0, str(shared_path))

from iec_type_converter import IECTypeConverter


class TestClampToType:
    """Tests for value clamping functionality."""

    @pytest.mark.parametrize(
        "value,type_name,expected",
        [
            # SINT bounds (-128 to 127)
            (0, "SINT", 0),
            (127, "SINT", 127),
            (128, "SINT", 127),  # Clamped to max
            (-128, "SINT", -128),
            (-129, "SINT", -128),  # Clamped to min
            (1000, "SINT", 127),
            # USINT/BYTE bounds (0 to 255)
            (0, "USINT", 0),
            (255, "USINT", 255),
            (256, "USINT", 255),  # Clamped
            (-1, "USINT", 0),  # Clamped
            (0, "BYTE", 0),
            (255, "BYTE", 255),
            # INT bounds (-32768 to 32767)
            (0, "INT", 0),
            (32767, "INT", 32767),
            (32768, "INT", 32767),  # Clamped
            (-32768, "INT", -32768),
            (-32769, "INT", -32768),  # Clamped
            # UINT/WORD bounds (0 to 65535)
            (0, "UINT", 0),
            (65535, "UINT", 65535),
            (65536, "UINT", 65535),  # Clamped
            (-1, "WORD", 0),  # Clamped
            # DINT bounds (-2147483648 to 2147483647)
            (0, "DINT", 0),
            (2147483647, "DINT", 2147483647),
            (-2147483648, "DINT", -2147483648),
            # UDINT/DWORD bounds (0 to 4294967295)
            (0, "UDINT", 0),
            (4294967295, "UDINT", 4294967295),
            (4294967296, "DWORD", 4294967295),  # Clamped
            # LINT bounds
            (0, "LINT", 0),
            (9223372036854775807, "LINT", 9223372036854775807),
            (-9223372036854775808, "LINT", -9223372036854775808),
            # ULINT/LWORD bounds
            (0, "ULINT", 0),
            (18446744073709551615, "ULINT", 18446744073709551615),
            (-1, "LWORD", 0),  # Clamped
        ],
    )
    def test_clamp_integer_types(self, value, type_name, expected):
        """Test clamping for all integer types."""
        result = IECTypeConverter.clamp_to_type(value, type_name)
        assert result == expected

    def test_clamp_signed_conversion(self):
        """Verify signed values are correctly represented."""
        # -1 as SINT should stay -1, not become 255
        result = IECTypeConverter.clamp_to_type(-1, "SINT")
        assert result == -1

        # -1 as INT should stay -1
        result = IECTypeConverter.clamp_to_type(-1, "INT")
        assert result == -1

    def test_clamp_case_insensitive(self):
        """Verify type name is case-insensitive."""
        assert IECTypeConverter.clamp_to_type(100, "sint") == 100
        assert IECTypeConverter.clamp_to_type(100, "SINT") == 100
        assert IECTypeConverter.clamp_to_type(100, "SiNt") == 100

    def test_clamp_alias_support(self):
        """Verify type aliases work correctly."""
        # INT32 is alias for DINT
        assert IECTypeConverter.clamp_to_type(2147483648, "INT32") == 2147483647

    def test_clamp_unknown_type_raises(self):
        """Verify ValueError for unknown types."""
        with pytest.raises(ValueError, match="Unknown IEC type"):
            IECTypeConverter.clamp_to_type(100, "UNKNOWN_TYPE")

    def test_clamp_float_returns_float(self):
        """Verify REAL/LREAL return float values."""
        result = IECTypeConverter.clamp_to_type(3.14, "REAL")
        assert isinstance(result, float)
        assert result == 3.14

        result = IECTypeConverter.clamp_to_type(3.14159265358979, "LREAL")
        assert isinstance(result, float)

    def test_clamp_string_returns_string(self):
        """Verify STRING type returns string."""
        result = IECTypeConverter.clamp_to_type(123, "STRING")
        assert result == "123"


class TestCoerceToType:
    """Tests for value coercion functionality."""

    def test_coerce_bool_from_various_types(self):
        """Test BOOL coercion from various input types."""
        assert IECTypeConverter.coerce_to_type(True, "BOOL") == 1
        assert IECTypeConverter.coerce_to_type(False, "BOOL") == 0
        assert IECTypeConverter.coerce_to_type(1, "BOOL") == 1
        assert IECTypeConverter.coerce_to_type(0, "BOOL") == 0
        assert IECTypeConverter.coerce_to_type(100, "BOOL") == 1
        assert IECTypeConverter.coerce_to_type("true", "BOOL") == 1
        assert IECTypeConverter.coerce_to_type("false", "BOOL") == 0
        assert IECTypeConverter.coerce_to_type("yes", "BOOL") == 1
        assert IECTypeConverter.coerce_to_type("no", "BOOL") == 0


class TestFloatConversion:
    """Tests for float <-> integer bit representation conversion."""

    def test_real_to_int_repr(self):
        """Test REAL to integer representation."""
        # 3.14 as 32-bit float
        int_repr = IECTypeConverter.float_to_int_repr(3.14, "REAL")
        # Verify it can be converted back
        back = IECTypeConverter.int_repr_to_float(int_repr, "REAL")
        assert abs(back - 3.14) < 0.0001

    def test_lreal_to_int_repr(self):
        """Test LREAL to integer representation."""
        # Pi as 64-bit double
        int_repr = IECTypeConverter.float_to_int_repr(3.141592653589793, "LREAL")
        back = IECTypeConverter.int_repr_to_float(int_repr, "LREAL")
        assert abs(back - 3.141592653589793) < 1e-10

    def test_real_zero(self):
        """Test zero value for REAL."""
        int_repr = IECTypeConverter.float_to_int_repr(0.0, "REAL")
        back = IECTypeConverter.int_repr_to_float(int_repr, "REAL")
        assert back == 0.0

    def test_negative_real(self):
        """Test negative float value."""
        int_repr = IECTypeConverter.float_to_int_repr(-123.456, "REAL")
        back = IECTypeConverter.int_repr_to_float(int_repr, "REAL")
        assert abs(back - (-123.456)) < 0.001

    def test_float_conversion_non_float_type_raises(self):
        """Verify error for non-float types."""
        with pytest.raises(ValueError, match="not a float type"):
            IECTypeConverter.float_to_int_repr(3.14, "INT")

        with pytest.raises(ValueError, match="not a float type"):
            IECTypeConverter.int_repr_to_float(12345, "INT")


class TestRegisterConversion:
    """Tests for Modbus register conversion."""

    def test_single_register_byte(self):
        """Test 8-bit value from single register."""
        # BYTE: lower 8 bits of register
        result = IECTypeConverter.registers_to_value([0x12AB], "BYTE")
        assert result == 0xAB

    def test_single_register_word(self):
        """Test 16-bit value from single register."""
        result = IECTypeConverter.registers_to_value([0x1234], "WORD")
        assert result == 0x1234

    def test_two_registers_dword_little_endian(self):
        """Test 32-bit value from two registers (little-endian)."""
        # Little-endian: [low, high]
        result = IECTypeConverter.registers_to_value([0x5678, 0x1234], "DWORD", big_endian=False)
        assert result == 0x12345678

    def test_two_registers_dword_big_endian(self):
        """Test 32-bit value from two registers (big-endian)."""
        # Big-endian: [high, low]
        result = IECTypeConverter.registers_to_value([0x1234, 0x5678], "DWORD", big_endian=True)
        assert result == 0x12345678

    def test_four_registers_lword_little_endian(self):
        """Test 64-bit value from four registers (little-endian)."""
        result = IECTypeConverter.registers_to_value(
            [0x0123, 0x4567, 0x89AB, 0xCDEF],
            "LWORD",
            big_endian=False,
        )
        # Little-endian: reg[0] is lowest
        expected = 0xCDEF89AB45670123
        assert result == expected

    def test_four_registers_lword_big_endian(self):
        """Test 64-bit value from four registers (big-endian)."""
        result = IECTypeConverter.registers_to_value(
            [0x0123, 0x4567, 0x89AB, 0xCDEF], "LWORD", big_endian=True
        )
        # Big-endian: reg[0] is highest
        expected = 0x0123456789ABCDEF
        assert result == expected

    def test_signed_dint_conversion(self):
        """Test signed 32-bit value handling."""
        # -1 as unsigned 32-bit is 0xFFFFFFFF
        result = IECTypeConverter.registers_to_value([0xFFFF, 0xFFFF], "DINT", big_endian=False)
        assert result == -1

    def test_signed_lint_conversion(self):
        """Test signed 64-bit value handling."""
        # -1 as unsigned 64-bit
        result = IECTypeConverter.registers_to_value(
            [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF], "LINT", big_endian=False
        )
        assert result == -1

    def test_size_code_support(self):
        """Test backward compatibility with size codes."""
        # 'W' is WORD
        result = IECTypeConverter.registers_to_value([0x1234], "W")
        assert result == 0x1234

        # 'D' is DWORD
        result = IECTypeConverter.registers_to_value([0x5678, 0x1234], "D", big_endian=False)
        assert result == 0x12345678

    def test_insufficient_registers_raises(self):
        """Verify error when not enough registers provided."""
        with pytest.raises(ValueError, match="Need at least 2 registers"):
            IECTypeConverter.registers_to_value([0x1234], "DWORD")

        with pytest.raises(ValueError, match="Need at least 4 registers"):
            IECTypeConverter.registers_to_value([0x1234, 0x5678], "LWORD")


class TestValueToRegisters:
    """Tests for value to Modbus register conversion."""

    def test_byte_to_register(self):
        """Test 8-bit value to single register."""
        result = IECTypeConverter.value_to_registers(0xAB, "BYTE")
        assert result == [0xAB]

    def test_word_to_register(self):
        """Test 16-bit value to single register."""
        result = IECTypeConverter.value_to_registers(0x1234, "WORD")
        assert result == [0x1234]

    def test_dword_to_registers_little_endian(self):
        """Test 32-bit value to two registers (little-endian)."""
        result = IECTypeConverter.value_to_registers(0x12345678, "DWORD", big_endian=False)
        assert result == [0x5678, 0x1234]

    def test_dword_to_registers_big_endian(self):
        """Test 32-bit value to two registers (big-endian)."""
        result = IECTypeConverter.value_to_registers(0x12345678, "DWORD", big_endian=True)
        assert result == [0x1234, 0x5678]

    def test_lword_to_registers_little_endian(self):
        """Test 64-bit value to four registers (little-endian)."""
        result = IECTypeConverter.value_to_registers(0x0123456789ABCDEF, "LWORD", big_endian=False)
        assert result == [0xCDEF, 0x89AB, 0x4567, 0x0123]

    def test_lword_to_registers_big_endian(self):
        """Test 64-bit value to four registers (big-endian)."""
        result = IECTypeConverter.value_to_registers(0x0123456789ABCDEF, "LWORD", big_endian=True)
        assert result == [0x0123, 0x4567, 0x89AB, 0xCDEF]

    def test_signed_negative_value(self):
        """Test negative signed value conversion."""
        # -1 as DINT
        result = IECTypeConverter.value_to_registers(-1, "DINT", big_endian=False)
        assert result == [0xFFFF, 0xFFFF]

    def test_roundtrip_conversion(self):
        """Verify value -> registers -> value roundtrip."""
        original = 0x12345678
        registers = IECTypeConverter.value_to_registers(original, "DWORD", big_endian=True)
        recovered = IECTypeConverter.registers_to_value(registers, "DWORD", big_endian=True)
        assert recovered == original

    def test_roundtrip_signed(self):
        """Verify signed value roundtrip."""
        original = -12345
        registers = IECTypeConverter.value_to_registers(original, "DINT", big_endian=False)
        recovered = IECTypeConverter.registers_to_value(registers, "DINT", big_endian=False)
        assert recovered == original


class TestTimeConversion:
    """Tests for TIME type conversions."""

    def test_timespec_to_milliseconds(self):
        """Test IEC_TIMESPEC to milliseconds conversion."""
        # 1 second, 500 million nanoseconds = 1500 ms
        result = IECTypeConverter.timespec_to_milliseconds(1, 500_000_000)
        assert result == 1500

        # 0 seconds, 0 nanoseconds = 0 ms
        result = IECTypeConverter.timespec_to_milliseconds(0, 0)
        assert result == 0

        # 10 seconds, 0 nanoseconds = 10000 ms
        result = IECTypeConverter.timespec_to_milliseconds(10, 0)
        assert result == 10000

    def test_milliseconds_to_timespec(self):
        """Test milliseconds to IEC_TIMESPEC conversion."""
        # 1500 ms = 1 second, 500 million nanoseconds
        tv_sec, tv_nsec = IECTypeConverter.milliseconds_to_timespec(1500)
        assert tv_sec == 1
        assert tv_nsec == 500_000_000

        # 0 ms
        tv_sec, tv_nsec = IECTypeConverter.milliseconds_to_timespec(0)
        assert tv_sec == 0
        assert tv_nsec == 0

    def test_timespec_to_datetime_tod(self):
        """Test TOD (time of day) conversion."""
        # 3600 seconds = 1 hour since midnight
        dt = IECTypeConverter.timespec_to_datetime(3600, 0, "TOD")
        assert dt.hour == 1
        assert dt.minute == 0
        assert dt.second == 0

    def test_timespec_to_datetime_date(self):
        """Test DATE conversion."""
        # Epoch timestamp for 2024-01-01
        timestamp = 1704067200  # 2024-01-01 00:00:00 UTC
        dt = IECTypeConverter.timespec_to_datetime(timestamp, 0, "DATE")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 0  # Time should be zeroed

    def test_timespec_to_datetime_dt(self):
        """Test DT (date and time) conversion."""
        # Epoch timestamp with microseconds
        timestamp = 1704067200  # 2024-01-01 00:00:00 UTC
        dt = IECTypeConverter.timespec_to_datetime(timestamp, 123_000_000, "DT")
        assert dt.year == 2024
        assert dt.microsecond == 123000

    def test_datetime_to_timespec_tod(self):
        """Test datetime to TOD conversion."""
        dt = datetime(2024, 1, 1, 13, 30, 45, 500000, tzinfo=timezone.utc)
        tv_sec, tv_nsec = IECTypeConverter.datetime_to_timespec(dt, "TOD")
        # 13:30:45 = 13*3600 + 30*60 + 45 = 48645 seconds since midnight
        assert tv_sec == 48645
        assert tv_nsec == 500_000_000  # 500000 microseconds = 500M nanoseconds

    def test_datetime_to_timespec_date(self):
        """Test datetime to DATE conversion."""
        dt = datetime(2024, 1, 1, 13, 30, 45, tzinfo=timezone.utc)
        tv_sec, tv_nsec = IECTypeConverter.datetime_to_timespec(dt, "DATE")
        # Should be midnight of that day
        expected_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert tv_sec == int(expected_dt.timestamp())
        assert tv_nsec == 0


class TestDefaultValues:
    """Tests for default value generation."""

    @pytest.mark.parametrize(
        "type_name,expected",
        [
            ("BOOL", False),
            ("SINT", 0),
            ("INT", 0),
            ("DINT", 0),
            ("REAL", 0.0),
            ("LREAL", 0.0),
            ("STRING", ""),
            ("TIME", (0, 0)),
            ("DATE", (0, 0)),
            ("TOD", (0, 0)),
            ("DT", (0, 0)),
        ],
    )
    def test_default_values(self, type_name, expected):
        """Verify correct default values for all types."""
        result = IECTypeConverter.get_default_value(type_name)
        assert result == expected

    def test_protocol_default_opcua_time(self):
        """Test OPC-UA protocol default for TIME types."""
        # TIME in OPC-UA is milliseconds
        result = IECTypeConverter.get_default_value_for_protocol("TIME", "opcua")
        assert result == 0

        # DATE/TOD/DT in OPC-UA is DateTime
        result = IECTypeConverter.get_default_value_for_protocol("DATE", "opcua")
        assert isinstance(result, datetime)


class TestEndianSwap:
    """Tests for endianness swapping."""

    def test_swap_16(self):
        """Test 16-bit endianness swap."""
        assert IECTypeConverter.swap_endianness_16(0x1234) == 0x3412

    def test_swap_32(self):
        """Test 32-bit endianness swap."""
        assert IECTypeConverter.swap_endianness_32(0x12345678) == 0x78563412

    def test_swap_64(self):
        """Test 64-bit endianness swap."""
        assert IECTypeConverter.swap_endianness_64(0x0123456789ABCDEF) == 0xEFCDAB8967452301

    def test_swap_by_type(self):
        """Test endianness swap based on type."""
        assert IECTypeConverter.swap_endianness(0x1234, "INT") == 0x3412
        assert IECTypeConverter.swap_endianness(0x12345678, "DINT") == 0x78563412


class TestUtilityMethods:
    """Tests for utility methods."""

    def test_is_type_signed(self):
        """Test signed type detection."""
        assert IECTypeConverter.is_type_signed("SINT") is True
        assert IECTypeConverter.is_type_signed("INT") is True
        assert IECTypeConverter.is_type_signed("DINT") is True
        assert IECTypeConverter.is_type_signed("LINT") is True
        assert IECTypeConverter.is_type_signed("USINT") is False
        assert IECTypeConverter.is_type_signed("UINT") is False
        assert IECTypeConverter.is_type_signed("BYTE") is False
        assert IECTypeConverter.is_type_signed("WORD") is False

    def test_get_size_bytes(self):
        """Test size in bytes retrieval."""
        assert IECTypeConverter.get_size_bytes("SINT") == 1
        assert IECTypeConverter.get_size_bytes("INT") == 2
        assert IECTypeConverter.get_size_bytes("DINT") == 4
        assert IECTypeConverter.get_size_bytes("LINT") == 8
        assert IECTypeConverter.get_size_bytes("UNKNOWN") == 0

    def test_get_register_count(self):
        """Test register count retrieval."""
        assert IECTypeConverter.get_register_count("BOOL") == 0
        assert IECTypeConverter.get_register_count("BYTE") == 1
        assert IECTypeConverter.get_register_count("WORD") == 1
        assert IECTypeConverter.get_register_count("DWORD") == 2
        assert IECTypeConverter.get_register_count("LWORD") == 4
        # Size codes
        assert IECTypeConverter.get_register_count("B") == 1
        assert IECTypeConverter.get_register_count("W") == 1
        assert IECTypeConverter.get_register_count("D") == 2
        assert IECTypeConverter.get_register_count("L") == 4
