# Source this optional user-prefix setup. RR_ENV_ROOT names the isolated
# environment directory containing prefix/, venv/, tt-metal/, simulator/.
: "${RR_ENV_ROOT:?Set RR_ENV_ROOT before sourcing this file}"
export PATH="$RR_ENV_ROOT/wrappers:$RR_ENV_ROOT/venv/bin:$RR_ENV_ROOT/prefix/usr/bin:$RR_ENV_ROOT/prefix/usr/lib/llvm-20/bin:$PATH"
export LD_LIBRARY_PATH="$RR_ENV_ROOT/prefix/usr/lib/aarch64-linux-gnu:$RR_ENV_ROOT/prefix/usr/lib/llvm-20/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CMAKE_PREFIX_PATH="$RR_ENV_ROOT/prefix/usr${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="$RR_ENV_ROOT/prefix/usr/lib/aarch64-linux-gnu/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export CXXFLAGS="--gcc-toolchain=$RR_ENV_ROOT/prefix/usr -isystem $RR_ENV_ROOT/prefix/usr/include -isystem $RR_ENV_ROOT/prefix/usr/include/aarch64-linux-gnu"
export CFLAGS="--gcc-toolchain=$RR_ENV_ROOT/prefix/usr -isystem $RR_ENV_ROOT/prefix/usr/include -isystem $RR_ENV_ROOT/prefix/usr/include/aarch64-linux-gnu"
export LDFLAGS="-L$RR_ENV_ROOT/prefix/usr/lib/aarch64-linux-gnu -Wl,-rpath,$RR_ENV_ROOT/prefix/usr/lib/aarch64-linux-gnu"
export TT_METAL_HOME="$RR_ENV_ROOT/tt-metal"
export TT_METAL_SIMULATOR="$RR_ENV_ROOT/simulator/libttsim_wh_aarch64.so"
export TT_METAL_SLOW_DISPATCH_MODE=1
export TT_METAL_DISABLE_SFPLOADMACRO=1
export TT_METAL_CACHE="$RR_ENV_ROOT/kernel-cache"
export XDG_CACHE_HOME="$RR_ENV_ROOT/xdg-cache"
