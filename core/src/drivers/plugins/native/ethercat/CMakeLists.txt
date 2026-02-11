# CMakeLists.txt for EtherCAT Plugin
# Builds a self-contained EtherCAT plugin with SOEM library included

cmake_minimum_required(VERSION 3.10)
project(ethercat_plugin C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Determine OpenPLC root directory for finding common headers
# When building standalone: calculate from plugin location
# When building from main project: pass -DOPENPLC_ROOT=<path>
if(NOT DEFINED OPENPLC_ROOT)
    get_filename_component(OPENPLC_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../../" ABSOLUTE)
endif()

message(STATUS "EtherCAT Plugin - OpenPLC root: ${OPENPLC_ROOT}")

# =============================================================================
# SOEM Library (built via add_subdirectory for proper ec_options.h generation)
# =============================================================================

set(SOEM_DIR ${CMAKE_CURRENT_SOURCE_DIR}/libs/soem)
set(SOEM_BUILD_SAMPLES OFF CACHE BOOL "Disable SOEM samples" FORCE)
add_subdirectory(${SOEM_DIR} ${CMAKE_CURRENT_BINARY_DIR}/soem)

# =============================================================================
# cJSON Library (embedded for JSON configuration parsing)
# =============================================================================

set(CJSON_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/cjson/cJSON.c
)

# =============================================================================
# Plugin Source Files
# =============================================================================

set(PLUGIN_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/ethercat_plugin.c
    ${CMAKE_CURRENT_SOURCE_DIR}/ethercat_config.c
    ${CMAKE_CURRENT_SOURCE_DIR}/ethercat_master.c
    ${CMAKE_CURRENT_SOURCE_DIR}/ethercat_io.c
    ${OPENPLC_ROOT}/core/src/drivers/plugins/native/plugin_logger.c
)

# =============================================================================
# Create Shared Library
# =============================================================================

add_library(ethercat_plugin SHARED
    ${CJSON_SOURCES}
    ${PLUGIN_SOURCES}
)

# =============================================================================
# Include Directories
# =============================================================================

target_include_directories(ethercat_plugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/cjson
    ${OPENPLC_ROOT}/core/src/drivers
    ${OPENPLC_ROOT}/core/src/drivers/plugins/native
    ${OPENPLC_ROOT}/core/src/lib
)

# =============================================================================
# Compiler Options
# =============================================================================

target_compile_options(ethercat_plugin PRIVATE
    -fPIC
    -Wno-unused-parameter
    -Wno-sign-compare
)

# =============================================================================
# Link Libraries
# =============================================================================

target_link_libraries(ethercat_plugin PRIVATE
    soem
    ${CMAKE_DL_LIBS}
)

# =============================================================================
# Output Settings
# =============================================================================

set_target_properties(ethercat_plugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/plugins
    PREFIX "lib"
    OUTPUT_NAME "ethercat_plugin"
)

# On Linux, use .so extension
if(UNIX)
    set_target_properties(ethercat_plugin PROPERTIES SUFFIX ".so")
endif()

# =============================================================================
# Install Target
# =============================================================================

install(TARGETS ethercat_plugin
    LIBRARY DESTINATION lib/openplc/plugins
    RUNTIME DESTINATION lib/openplc/plugins
)
