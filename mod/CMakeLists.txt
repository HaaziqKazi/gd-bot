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

project(gdrl-probe VERSION 0.1.0)

add_library(${PROJECT_NAME} SHARED
    src/main.cpp
    src/experiments.cpp
)

if (NOT DEFINED ENV{GEODE_SDK})
    message(FATAL_ERROR "Unable to find Geode SDK! Set the GEODE_SDK environment variable.")
else()
    message(STATUS "Found Geode: $ENV{GEODE_SDK}")
endif()

add_subdirectory($ENV{GEODE_SDK} ${CMAKE_CURRENT_BINARY_DIR}/geode)

setup_geode_mod(${PROJECT_NAME})
