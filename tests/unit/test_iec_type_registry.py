"""
Unit tests for IEC 61131-3 Type Registry

Tests the iec_type_registry module which provides centralized type metadata
for all IEC 61131-3 data types.
"""

import ctypes
import sys
from pathlib import Path

import pytest

# Add shared module to path
shared_path = Path(__file__).parent.parent.parent / "core/src/drivers/plugins/python/shared"
sys.path.insert(0, str(shared_path))

from iec_type_registry import (
    ALL_TYPES,
    BIT_STRING_TYPES,
    FLOAT_TYPES,
    INTEGER_TYPES,
    SIGNED_INTEGER_TYPES,
    TIME_TYPES,
    UNSIGNED_INTEGER_TYPES,
    IECTypeInfo,
    get_all_types,
    get_canonical_name,
    get_ctype_class,
    get_register_count,
    get_size_code,
    get_type_bounds,
    get_type_info,
    get_types_by_size,
    is_valid_type,
    size_code_to_type_name,
)


class TestTypeRegistry:
    """Tests for the type registry core functions."""

    def test_all_22_types_present(self):
        """Verify all 22 IEC 61131-3 types are in the registry."""
        expected_types = {
            "BOOL",
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
            "REAL",
            "LREAL",
            "TIME",
            "DATE",
            "TOD",
            "DT",
            "STRING",
        }
        # Note: Some types share the same underlying storage (e.g., BYTE/USINT)
        # but are distinct types
        assert expected_types.issubset(ALL_TYPES)

    def test_get_type_info_returns_correct_type(self):
        """Verify get_type_info returns correct IECTypeInfo objects."""
        info = get_type_info("SINT")
        assert info is not None
        assert isinstance(info, IECTypeInfo)
        assert info.name == "SINT"
        assert info.size_bits == 8
        assert info.size_bytes == 1
        assert info.signed is True
        assert info.min_value == -128
        assert info.max_value == 127

    def test_get_type_info_case_insensitive(self):
        """Verify type lookup is case-insensitive."""
        info1 = get_type_info("sint")
        info2 = get_type_info("SINT")
        info3 = get_type_info("SiNt")
        assert info1 == info2 == info3

    def test_get_type_info_unknown_type(self):
        """Verify None is returned for unknown types."""
        assert get_type_info("UNKNOWN_TYPE") is None
        assert get_type_info("") is None

    def test_aliases_resolve_correctly(self):
        """Verify type aliases resolve to canonical types."""
        # INT32 should resolve to DINT
        assert get_canonical_name("INT32") == "DINT"
        # FLOAT should resolve to REAL
        assert get_canonical_name("FLOAT") == "REAL"
        # DOUBLE should resolve to LREAL
        assert get_canonical_name("DOUBLE") == "LREAL"
        # TIME_OF_DAY should resolve to TOD
        assert get_canonical_name("TIME_OF_DAY") == "TOD"
        # DATE_AND_TIME should resolve to DT
        assert get_canonical_name("DATE_AND_TIME") == "DT"

    def test_is_valid_type(self):
        """Verify is_valid_type correctly identifies valid types."""
        assert is_valid_type("SINT") is True
        assert is_valid_type("INT32") is True  # Alias
        assert is_valid_type("UNKNOWN") is False
        assert is_valid_type("") is False


class TestTypeBounds:
    """Tests for type value bounds."""

    @pytest.mark.parametrize(
        "type_name,expected_min,expected_max",
        [
            ("BOOL", 0, 1),
            ("SINT", -128, 127),
            ("USINT", 0, 255),
            ("BYTE", 0, 255),
            ("INT", -32768, 32767),
            ("UINT", 0, 65535),
            ("WORD", 0, 65535),
            ("DINT", -2147483648, 2147483647),
            ("UDINT", 0, 4294967295),
            ("DWORD", 0, 4294967295),
            ("LINT", -9223372036854775808, 9223372036854775807),
            ("ULINT", 0, 18446744073709551615),
            ("LWORD", 0, 18446744073709551615),
        ],
    )
    def test_integer_type_bounds(self, type_name, expected_min, expected_max):
        """Verify correct bounds for all integer types."""
        bounds = get_type_bounds(type_name)
        assert bounds is not None
        assert bounds[0] == expected_min
        assert bounds[1] == expected_max

    def test_float_types_have_integer_bounds(self):
        """Verify float types report integer representation bounds."""
        real_bounds = get_type_bounds("REAL")
        assert real_bounds is not None
        # REAL uses 32-bit storage
        assert real_bounds == (-2147483648, 2147483647)

        lreal_bounds = get_type_bounds("LREAL")
        assert lreal_bounds is not None
        # LREAL uses 64-bit storage
        assert lreal_bounds == (-9223372036854775808, 9223372036854775807)


class TestTypeSizes:
    """Tests for type size information."""

    @pytest.mark.parametrize(
        "type_name,expected_bytes",
        [
            ("BOOL", 1),
            ("SINT", 1),
            ("USINT", 1),
            ("BYTE", 1),
            ("INT", 2),
            ("UINT", 2),
            ("WORD", 2),
            ("DINT", 4),
            ("UDINT", 4),
            ("DWORD", 4),
            ("REAL", 4),
            ("LINT", 8),
            ("ULINT", 8),
            ("LWORD", 8),
            ("LREAL", 8),
            ("TIME", 8),
            ("DATE", 8),
            ("TOD", 8),
            ("DT", 8),
            ("STRING", 127),
        ],
    )
    def test_type_sizes(self, type_name, expected_bytes):
        """Verify correct size in bytes for all types."""
        info = get_type_info(type_name)
        assert info is not None
        assert info.size_bytes == expected_bytes


class TestRegisterCounts:
    """Tests for Modbus register count calculation."""

    @pytest.mark.parametrize(
        "type_name,expected_count",
        [
            ("BOOL", 0),  # BOOL uses coils, not registers
            ("SINT", 1),
            ("USINT", 1),
            ("BYTE", 1),
            ("INT", 1),
            ("UINT", 1),
            ("WORD", 1),
            ("DINT", 2),
            ("UDINT", 2),
            ("DWORD", 2),
            ("REAL", 2),
            ("LINT", 4),
            ("ULINT", 4),
            ("LWORD", 4),
            ("LREAL", 4),
            ("TIME", 4),
        ],
    )
    def test_register_counts(self, type_name, expected_count):
        """Verify correct Modbus register count for all types."""
        assert get_register_count(type_name) == expected_count


class TestSizeCodes:
    """Tests for IEC size code mapping."""

    @pytest.mark.parametrize(
        "type_name,expected_code",
        [
            ("BOOL", "X"),
            ("SINT", "B"),
            ("USINT", "B"),
            ("BYTE", "B"),
            ("INT", "W"),
            ("UINT", "W"),
            ("WORD", "W"),
            ("DINT", "D"),
            ("UDINT", "D"),
            ("DWORD", "D"),
            ("REAL", "D"),
            ("LINT", "L"),
            ("ULINT", "L"),
            ("LWORD", "L"),
            ("LREAL", "L"),
            ("STRING", "S"),
        ],
    )
    def test_size_codes(self, type_name, expected_code):
        """Verify correct IEC size codes for all types."""
        assert get_size_code(type_name) == expected_code

    @pytest.mark.parametrize(
        "size_code,expected_type",
        [
            ("X", "BOOL"),
            ("B", "BYTE"),
            ("W", "WORD"),
            ("D", "DWORD"),
            ("L", "LWORD"),
        ],
    )
    def test_size_code_to_type(self, size_code, expected_type):
        """Verify size code to type name conversion."""
        assert size_code_to_type_name(size_code) == expected_type


class TestCtypeClasses:
    """Tests for ctypes class mapping."""

    @pytest.mark.parametrize(
        "type_name,expected_ctype",
        [
            ("BOOL", ctypes.c_uint8),
            ("SINT", ctypes.c_int8),
            ("USINT", ctypes.c_uint8),
            ("BYTE", ctypes.c_uint8),
            ("INT", ctypes.c_int16),
            ("UINT", ctypes.c_uint16),
            ("WORD", ctypes.c_uint16),
            ("DINT", ctypes.c_int32),
            ("UDINT", ctypes.c_uint32),
            ("DWORD", ctypes.c_uint32),
            ("LINT", ctypes.c_int64),
            ("ULINT", ctypes.c_uint64),
            ("LWORD", ctypes.c_uint64),
            ("REAL", ctypes.c_float),
            ("LREAL", ctypes.c_double),
        ],
    )
    def test_ctype_classes(self, type_name, expected_ctype):
        """Verify correct ctypes classes for all types."""
        assert get_ctype_class(type_name) == expected_ctype


class TestTypeCategories:
    """Tests for type category sets."""

    def test_integer_types_set(self):
        """Verify INTEGER_TYPES contains all integer types."""
        assert "SINT" in INTEGER_TYPES
        assert "INT" in INTEGER_TYPES
        assert "DINT" in INTEGER_TYPES
        assert "LINT" in INTEGER_TYPES
        assert "REAL" not in INTEGER_TYPES  # Float
        assert "BOOL" not in INTEGER_TYPES

    def test_signed_vs_unsigned_partition(self):
        """Verify signed/unsigned sets are mutually exclusive and complete."""
        assert SIGNED_INTEGER_TYPES.isdisjoint(UNSIGNED_INTEGER_TYPES)
        assert SIGNED_INTEGER_TYPES | UNSIGNED_INTEGER_TYPES == INTEGER_TYPES

    def test_float_types_set(self):
        """Verify FLOAT_TYPES contains exactly REAL and LREAL."""
        assert FLOAT_TYPES == {"REAL", "LREAL"}

    def test_time_types_set(self):
        """Verify TIME_TYPES contains all time-related types."""
        assert TIME_TYPES == {"TIME", "DATE", "TOD", "DT"}

    def test_bit_string_types_set(self):
        """Verify BIT_STRING_TYPES contains byte/word/dword/lword."""
        assert BIT_STRING_TYPES == {"BYTE", "WORD", "DWORD", "LWORD"}


class TestTypeLookupBySize:
    """Tests for looking up types by size."""

    def test_get_types_by_size_1_byte(self):
        """Verify 1-byte types are correctly identified."""
        one_byte_types = get_types_by_size(1)
        assert "BOOL" in one_byte_types
        assert "SINT" in one_byte_types
        assert "USINT" in one_byte_types
        assert "BYTE" in one_byte_types
        assert "INT" not in one_byte_types

    def test_get_types_by_size_2_bytes(self):
        """Verify 2-byte types are correctly identified."""
        two_byte_types = get_types_by_size(2)
        assert "INT" in two_byte_types
        assert "UINT" in two_byte_types
        assert "WORD" in two_byte_types
        assert "DINT" not in two_byte_types

    def test_get_types_by_size_4_bytes(self):
        """Verify 4-byte types are correctly identified."""
        four_byte_types = get_types_by_size(4)
        assert "DINT" in four_byte_types
        assert "UDINT" in four_byte_types
        assert "DWORD" in four_byte_types
        assert "REAL" in four_byte_types
        assert "LINT" not in four_byte_types

    def test_get_types_by_size_8_bytes(self):
        """Verify 8-byte types are correctly identified."""
        eight_byte_types = get_types_by_size(8)
        assert "LINT" in eight_byte_types
        assert "ULINT" in eight_byte_types
        assert "LWORD" in eight_byte_types
        assert "LREAL" in eight_byte_types
        assert "TIME" in eight_byte_types
        assert "DATE" in eight_byte_types
        assert "TOD" in eight_byte_types
        assert "DT" in eight_byte_types


class TestTypeInfoImmutability:
    """Tests for IECTypeInfo immutability."""

    def test_type_info_is_frozen(self):
        """Verify IECTypeInfo instances cannot be modified."""
        info = get_type_info("SINT")
        with pytest.raises(AttributeError):
            info.name = "MODIFIED"
        with pytest.raises(AttributeError):
            info.size_bits = 999

    def test_registry_copy_is_independent(self):
        """Verify get_all_types returns an independent copy."""
        registry1 = get_all_types()
        registry2 = get_all_types()
        # Modifying one should not affect the other
        del registry1["SINT"]
        assert "SINT" in registry2
