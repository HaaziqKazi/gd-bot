cmake_minimum_required(VERSION 3.21)

# These MUST be set before project() and before add_library() below.
#
# Geode's Platform.cmake sets sensible macOS defaults (universal, 11.0), but it
# only does so inside the SDK's own directory scope, which is added *after* our
# target is created. A target created first keeps whatever the toolchain
# defaulted to. Here that default deployment target was 10.15, which predates
# Apple Silicon, so CMake silently resolved the empty architecture list to
# x86_64 alone -- producing a mod that cannot load into the arm64 GD process.
set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "" FORCE)
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "" FORCE)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# OBJCXX is declared because App Nap suppression has no C API -- the only way to
# hold an NSProcessInfo activity is through Objective-C.
project(gdrl-probe VERSION 0.1.0 LANGUAGES C CXX OBJCXX)

add_library(${PROJECT_NAME} SHARED
    src/main.cpp
    src/experiments.cpp
    src/telemetry.cpp
    src/probes.cpp
    src/synth.cpp
    src/level_dump.cpp
    src/nonap.mm
)

# The activity token is retained by hand, so ARC must be off for this file.
# Left implicit it would depend on whatever the toolchain defaults to, which is
# exactly the kind of silent-default problem that has already cost time here.
set_source_files_properties(src/nonap.mm PROPERTIES COMPILE_FLAGS "-fno-objc-arc")

if (NOT DEFINED ENV{GEODE_SDK})
    message(FATAL_ERROR "Unable to find Geode SDK! Set the GEODE_SDK environment variable.")
else()
    message(STATUS "Found Geode: $ENV{GEODE_SDK}")
endif()

add_subdirectory($ENV{GEODE_SDK} ${CMAKE_CURRENT_BINARY_DIR}/geode)

target_link_libraries(${PROJECT_NAME} "-framework Foundation")

setup_geode_mod(${PROJECT_NAME})
