// SPDX-FileCopyrightText: © 2023 Tenstorrent USA, Inc.
// SPDX-FileCopyrightText: © 2026 Ziao Liu
// SPDX-License-Identifier: Apache-2.0
// Adapted from the pinned reduce_w.cpp; see docs/source-notes.md.

#include <cstdint>
#include "api/compute/reduce.h"
#include "api/dataflow/dataflow_buffer.h"
#include "experimental/kernel_args.h"
#include "tools/profiler/kernel_profiler.hpp"

void kernel_main() {
    DeviceZoneScopedN("row_reduce_compute");
    constexpr uint32_t Ht = get_arg(args::Ht);
    constexpr uint32_t Wt = get_arg(args::Wt);
    constexpr uint32_t NC = get_arg(args::NC);
    static_assert(NC == 1, "This project supports a single 2D matrix");
    static_assert(DST_ACCUM_MODE == 1, "The numeric contract requires FP32 Dest accumulation");
    static_assert(MATH_ONLY == 0, "The kernel must unpack actual input tiles");
    static_assert(REDUCE_DIM == ReduceDim::REDUCE_ROW);
    static_assert(REDUCE_OP == PoolType::SUM);

    DataflowBuffer input(dfb::in_data);
    DataflowBuffer scaler(dfb::in_scaler);
    DataflowBuffer output(dfb::out);
    compute_kernel_hw_startup(dfb::in_data, dfb::in_scaler, dfb::out);
    // Wormhole REDUCE_ROW SUM uses swapped MVMUL operands: scaler -> SrcA,
    // data -> SrcB. Keep this before reduce_init (reduce.h requires it).
    reconfig_data_format(dfb::in_scaler, dfb::in_data);
    reduce_init<REDUCE_OP, REDUCE_DIM, true>(dfb::in_data, dfb::in_scaler, dfb::out);

    scaler.wait_front(1);
    for (uint32_t tile_row = 0; tile_row < Ht; ++tile_row) {
        // Keep the pinned reduce_w.cpp acquire/wait/pack/commit/release order.
        // Dest slot 0 accumulates all Wt partial row sums before one BF16 pack.
        tile_regs_acquire();
        tile_regs_wait();
        for (uint32_t tile_col = 0; tile_col < Wt; ++tile_col) {
            input.wait_front(1);
            reduce_tile<REDUCE_OP, REDUCE_DIM, true>(dfb::in_data, dfb::in_scaler, 0, 0, 0);
            input.pop_front(1);
        }
        output.reserve_back(1);
        pack_tile(0, dfb::out);
        output.push_back(1);
        tile_regs_commit();
        tile_regs_release();
    }
    reduce_uninit();
}
