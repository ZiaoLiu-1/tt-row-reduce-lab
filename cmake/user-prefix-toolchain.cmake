# Preserve the pinned official ARM64 toolchain's semantics, then bind compiler
# paths explicitly. Repeated upstream CACHE INTERNAL assignments of bare names
# otherwise make CMake 4.0.2 discard the cache in this extracted prefix setup.
if(NOT DEFINED ENV{RR_ENV_ROOT} OR NOT DEFINED ENV{TT_METAL_HOME})
    message(FATAL_ERROR "Source activate_linux_prefix.sh before using this toolchain")
endif()
include("$ENV{TT_METAL_HOME}/cmake/aarch64-linux-clang-20-libstdcpp-toolchain.cmake")
set(CMAKE_C_COMPILER "$ENV{RR_ENV_ROOT}/prefix/usr/bin/clang-20" CACHE INTERNAL "C compiler")
set(CMAKE_CXX_COMPILER "$ENV{RR_ENV_ROOT}/prefix/usr/bin/clang++-20" CACHE INTERNAL "C++ compiler")
