# Include only after the project's CPU contract library exists. This file works
# both inside the pinned upstream tree and against a complete Metalium install.
if(NOT TARGET TT::Metalium)
    find_package(TT-Metalium REQUIRED)
endif()
get_filename_component(RR_PROJECT_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
execute_process(
    COMMAND git -C "${RR_PROJECT_ROOT}" rev-parse HEAD
    RESULT_VARIABLE RR_SOURCE_COMMIT_STATUS
    OUTPUT_VARIABLE RR_SOURCE_COMMIT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT RR_SOURCE_COMMIT_STATUS EQUAL 0)
    set(RR_SOURCE_COMMIT "unknown")
endif()
# Refresh this configure-time identity after committing code and before the final
# build. Runtime checkout identity is separately captured by the evidence runner.
add_executable(tt_row_reduce_metal "${RR_PROJECT_ROOT}/src/metal_main.cpp")
target_compile_features(tt_row_reduce_metal PRIVATE cxx_std_20)
target_compile_definitions(tt_row_reduce_metal PRIVATE
    RR_KERNEL_DIR="${RR_PROJECT_ROOT}/kernels"
    RR_UPSTREAM_COMMIT="89e1256c982a5b4739d173bcc446c8c748a44b40"
    RR_SOURCE_COMMIT="${RR_SOURCE_COMMIT}"
)
target_link_libraries(tt_row_reduce_metal PRIVATE TT::Metalium row_reduce_contract)
