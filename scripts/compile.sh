#!/bin/bash
set -euo pipefail

# Paths
ROOT="core/generated"
LIB_PATH="$ROOT/lib"
SRC_PATH="$ROOT"
BUILD_PATH="build"
PYTHON_INCLUDE_PATH="core/src/plc_app/include"
PYTHON_LOADER_SRC="core/src/plc_app/python_loader.c"

FLAGS="-w -O3 -fPIC"

check_required_files() {
    local missing_files=()

    if [ ! -f "$SRC_PATH/Config0.c" ]; then
        missing_files+=("$SRC_PATH/Config0.c")
    fi
    if [ ! -f "$SRC_PATH/Res0.c" ]; then
        missing_files+=("$SRC_PATH/Res0.c")
    fi
    if [ ! -f "$SRC_PATH/debug.c" ]; then
        missing_files+=("$SRC_PATH/debug.c")
    fi
    if [ ! -f "$SRC_PATH/glueVars.c" ]; then
        missing_files+=("$SRC_PATH/glueVars.c")
    fi
    if [ ! -d "$LIB_PATH" ]; then
        missing_files+=("$LIB_PATH (directory)")
    fi

    if [ ${#missing_files[@]} -ne 0 ]; then
        echo "[ERROR] Missing required source files:" >&2
        printf '  %s\n' "${missing_files[@]}" >&2
        exit 1
    fi
}

check_required_files

# Ensure build directory exists
mkdir -p "$BUILD_PATH"
if [ ! -d "$BUILD_PATH" ]; then
    echo "[ERROR] Failed to create build directory: $BUILD_PATH" >&2
    exit 1
fi

# On Cygwin/MSYS2, TCP/UDP communication blocks are not supported (the PE
# loader cannot resolve symbols from the host executable at dlopen time).
# Provide no-op stubs so programs using these blocks still compile and run
# — the blocks simply return -1 (failure) for every operation.
EXTRA_OBJS=""
case "$(uname -s)" in
    CYGWIN*|MSYS*|MINGW*)
        cat > "$BUILD_PATH/comm_stubs.c" << 'STUB'
#include <stdint.h>
#include <stddef.h>
int connect_to_tcp_server(uint8_t *a, uint16_t b, int c) { (void)a; (void)b; (void)c; return -1; }
int send_tcp_message(uint8_t *a, size_t b, int c) { (void)a; (void)b; (void)c; return -1; }
int receive_tcp_message(uint8_t *a, size_t b, int c) { (void)a; (void)b; (void)c; return -1; }
int close_tcp_connection(int a) { (void)a; return -1; }
STUB
        gcc $FLAGS -c "$BUILD_PATH/comm_stubs.c" -o "$BUILD_PATH/comm_stubs.o"
        EXTRA_OBJS="$BUILD_PATH/comm_stubs.o"
        ;;
esac

# Compile objects into build/
echo "[INFO] Compiling Config0.c..."
gcc $FLAGS -I "$LIB_PATH" -I "$PYTHON_INCLUDE_PATH" -include iec_python.h -c "$SRC_PATH/Config0.c" -o "$BUILD_PATH/Config0.o"
echo "[INFO] Compiling Res0.c..."
gcc $FLAGS -I "$LIB_PATH" -I "$PYTHON_INCLUDE_PATH" -include iec_python.h -c "$SRC_PATH/Res0.c" -o "$BUILD_PATH/Res0.o"
echo "[INFO] Compiling debug.c..."
gcc $FLAGS -I "$LIB_PATH" -c "$SRC_PATH/debug.c" -o "$BUILD_PATH/debug.o"
echo "[INFO] Compiling glueVars.c..."
gcc $FLAGS -I "$LIB_PATH" -DOPENPLC_V4 -c "$SRC_PATH/glueVars.c" -o "$BUILD_PATH/glueVars.o"
echo "[INFO] Compiling c_blocks_code.cpp..."
g++ $FLAGS -I "$LIB_PATH" -c "$SRC_PATH/c_blocks_code.cpp" -o "$BUILD_PATH/c_blocks_code.o"
echo "[INFO] Compiling python_loader.c..."
gcc $FLAGS -I "core/src/plc_app" -c "$PYTHON_LOADER_SRC" -o "$BUILD_PATH/python_loader.o"

# Link shared library into build/
echo "[INFO] Linking shared library..."
g++ $FLAGS -shared -o "$BUILD_PATH/new_libplc.so" "$BUILD_PATH/Config0.o" \
    "$BUILD_PATH/Res0.o" "$BUILD_PATH/debug.o" "$BUILD_PATH/glueVars.o" \
    "$BUILD_PATH/c_blocks_code.o" "$BUILD_PATH/python_loader.o" $EXTRA_OBJS -lpthread -lrt

# -----------------------------------------------------------------------
# Compile VPP plugin if source is present in the uploaded project
# -----------------------------------------------------------------------
VPP_PLUGIN_DIR="$ROOT/vpp_plugin"
VPP_CHECKSUM_FILE="$VPP_PLUGIN_DIR/checksum.sha256"
VPP_CACHED_CHECKSUM="$BUILD_PATH/vpp_plugin_checksum.sha256"

if [ -d "$VPP_PLUGIN_DIR" ] && [ -f "$VPP_PLUGIN_DIR/Makefile" ]; then
    NEEDS_COMPILE=1

    # Check if plugin source has changed since last compile (checksum caching)
    if [ -f "$VPP_CHECKSUM_FILE" ] && [ -f "$VPP_CACHED_CHECKSUM" ]; then
        if diff -q "$VPP_CHECKSUM_FILE" "$VPP_CACHED_CHECKSUM" > /dev/null 2>&1; then
            # Checksum matches — check if the compiled .so still exists
            if ls "$BUILD_PATH"/lib*_plugin.so 1>/dev/null 2>&1; then
                echo "[INFO] VPP plugin source unchanged (checksum match), skipping recompilation"
                NEEDS_COMPILE=0
            fi
        fi
    fi

    if [ "$NEEDS_COMPILE" -eq 1 ]; then
        echo "[INFO] Compiling VPP plugin from $VPP_PLUGIN_DIR..."
        PLUGIN_INCLUDE="-I $(pwd)/core/src/drivers -I $(pwd)/core/src/drivers/plugins/native -I $(pwd)/core/src/drivers/plugins/native/cjson -I $(pwd)/core/src/plc_app -I $(pwd)/core/lib"
        make -C "$VPP_PLUGIN_DIR" \
            INCLUDE_DIRS="$PLUGIN_INCLUDE" \
            OUTPUT_DIR="$(pwd)/$BUILD_PATH" \
            RUNTIME_ROOT="$(pwd)"

        if [ $? -ne 0 ]; then
            echo "[ERROR] VPP plugin compilation failed" >&2
            exit 1
        fi

        # Cache the checksum for future builds
        if [ -f "$VPP_CHECKSUM_FILE" ]; then
            cp "$VPP_CHECKSUM_FILE" "$VPP_CACHED_CHECKSUM"
        fi
        echo "[INFO] VPP plugin compiled successfully"
    fi
else
    # No VPP plugin in this upload — clean up any previously compiled VPP plugin
    if ls "$BUILD_PATH"/lib*_plugin.so 1>/dev/null 2>&1; then
        echo "[INFO] No VPP plugin in upload, removing previously compiled VPP plugin(s)"
        rm -f "$BUILD_PATH"/lib*_plugin.so "$VPP_CACHED_CHECKSUM"
    fi
fi
