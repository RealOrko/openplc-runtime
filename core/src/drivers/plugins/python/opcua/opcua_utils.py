"""OPC-UA plugin utility functions."""

import ctypes
import os
import sys
import struct
from typing import Any
from asyncua import ua

# Add directories to path for module access
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

# Import logging (handle both package and direct loading)
try:
    from .opcua_logging import log_info, log_warn, log_error
except ImportError:
    from opcua_logging import log_info, log_warn, log_error


def map_plc_to_opcua_type(plc_type: str) -> ua.VariantType:
    """Map plc datatype to OPC-UA VariantType."""
    type_mapping = {
        "BOOL": ua.VariantType.Boolean,
        "BYTE": ua.VariantType.Byte,
        "INT": ua.VariantType.Int16,
        "INT32": ua.VariantType.Int32,
        "DINT": ua.VariantType.Int32,
        "LINT": ua.VariantType.Int64,
        "FLOAT": ua.VariantType.Float,
        "STRING": ua.VariantType.String,
    }
    mapped_type = type_mapping.get(plc_type.upper(), ua.VariantType.Variant)
    return mapped_type


def convert_value_for_opcua(datatype: str, value: Any) -> Any:
    """Convert PLC debug variable value to OPC-UA compatible format."""
    # The debug utils return raw integer values based on variable size
    # Convert to appropriate OPC-UA types based on config datatype
    try:
        if datatype.upper() in ["BOOL", "Bool"]:
            # Ensure BOOL values are proper Python booleans
            if isinstance(value, bool):
                return value
            elif isinstance(value, (int, float)):
                return bool(value != 0)
            else:
                return bool(value)
        
        elif datatype.upper() in ["BYTE", "Byte"]:
            # Ensure proper uint8 type for OPC-UA compatibility
            return ctypes.c_uint8(max(0, min(255, int(value)))).value
        
        elif datatype.upper() in ["INT", "Int"]:
            # Ensure proper int16 type - critical for OPC-UA compatibility
            clamped_value = max(-32768, min(32767, int(value)))
            return ctypes.c_int16(clamped_value).value
        
        elif datatype.upper() in ["DINT", "Dint", "INT32", "Int32"]:
            # Ensure proper int32 type for OPC-UA compatibility
            clamped_value = max(-2147483648, min(2147483647, int(value)))
            return ctypes.c_int32(clamped_value).value
        
        elif datatype.upper() in ["LINT", "Lint"]:
            return int(value)  # int64
        
        elif datatype.upper() in ["FLOAT", "Float"]:
            # Float values are stored as integers in debug variables
            # Convert back to float if it's an integer representation
            if isinstance(value, int):
                try:
                    return struct.unpack('f', struct.pack('I', value))[0]
                except:
                    return float(value)
            return float(value)
        
        elif datatype.upper() in ["STRING", "String"]:
            return str(value)
        
        else:
            return value
            
    except (ValueError, TypeError, OverflowError) as e:
        # If conversion fails, return a safe default
        log_warn(f"Failed to convert value {value} to OPC-UA format for {datatype}: {e}")
        if datatype.upper() in ["BOOL", "Bool"]:
            return False
        elif datatype.upper() in ["FLOAT", "Float"]:
            return 0.0
        elif datatype.upper() in ["STRING", "String"]:
            return ""
        else:
            return 0


def convert_value_for_plc(datatype: str, value: Any) -> Any:
    """Convert OPC-UA value to PLC debug variable format."""
    # Handle different OPC-UA value types more robustly
    try:
        if datatype.upper() in ["BOOL", "Bool"]:
            # Convert any value to boolean, then to int (0/1)
            if isinstance(value, bool):
                return int(value)
            elif isinstance(value, (int, float)):
                return 1 if value != 0 else 0
            elif isinstance(value, str):
                return 1 if value.lower() in ['true', '1', 'yes', 'on'] else 0
            else:
                return int(bool(value))
        
        elif datatype.upper() in ["BYTE", "Byte"]:
            # Ensure proper uint8 type for PLC compatibility
            return ctypes.c_uint8(max(0, min(255, int(value)))).value
        
        elif datatype.upper() in ["INT", "Int"]:
            # Ensure proper int16 type for PLC compatibility  
            clamped_value = max(-32768, min(32767, int(value)))
            return ctypes.c_int16(clamped_value).value
        
        elif datatype.upper() in ["DINT", "Dint", "INT32", "Int32"]:
            # Ensure proper int32 type for PLC compatibility
            clamped_value = max(-2147483648, min(2147483647, int(value)))
            return ctypes.c_int32(clamped_value).value
        
        elif datatype.upper() in ["LINT", "Lint"]:
            return int(value)  # int64
        
        elif datatype.upper() in ["FLOAT", "Float"]:
            # Convert float to int representation for storage
            if isinstance(value, float):
                try:
                    return struct.unpack('I', struct.pack('f', value))[0]
                except:
                    return int(value)
            else:
                return int(float(value))
        
        elif datatype.upper() in ["STRING", "String"]:
            return str(value)
        
        else:
            # For unknown types, try to preserve the value
            return value
            
    except (ValueError, TypeError, OverflowError) as e:
        # If conversion fails, log and return a safe default
        log_warn(f"Failed to convert value {value} to {datatype}, using default: {e}")
        if datatype.upper() in ["BOOL", "Bool"]:
            return 0
        elif datatype.upper() in ["FLOAT", "Float"]:
            return 0
        elif datatype.upper() in ["STRING", "String"]:
            return ""
        else:
            return 0


def infer_var_type(size: int) -> str:
    """
    Infer variable type from size.

    Args:
        size: Size of the variable in bytes

    Returns:
        String indicating the inferred type or type category
    """
    if size == 1:
        return "BOOL_OR_SINT"
    elif size == 2:
        return "UINT16"
    elif size == 4:
        return "UINT32_OR_TIME"
    elif size == 8:
        return "UINT64_OR_TIME"
    elif size == 127:
        # IEC_STRING: 1 byte len + 126 bytes body = 127 bytes
        return "STRING"
    else:
        return "UNKNOWN"
