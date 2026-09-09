// SPDX-FileCopyrightText: © 2023 Tenstorrent USA, Inc.
// SPDX-FileCopyrightText: © 2026 Ziao Liu
// SPDX-License-Identifier: Apache-2.0
// Adapted from the pinned writer_unary_8bank_2_0.cpp; see docs/source-notes.md.

#include <cstdint>
#include "api/dataflow/dataflow_api.h"
#include "api/dataflow/dataflow_buffer.h"
#include "api/dataflow/noc.h"
#include "api/tensor/noc_traits.h"
#include "experimental/kernel_args.h"
#include "tools/profiler/kernel_profiler.hpp"

void kernel_main() {
    DeviceZoneScopedN("row_reduce_writer");
    Noc noc;
    DataflowBuffer output(dfb::in);
    const auto destination = TensorAccessor(tensor::dst_tensor);
    const uint32_t tile_bytes = output.get_entry_size();
    const uint32_t num_tiles = get_arg(args::num_tiles);
    for (uint32_t tile = 0; tile < num_tiles; ++tile) {
        output.wait_front(1);
        noc.async_write(output, destination, tile_bytes, {}, {.page_id = tile});
        noc.async_write_barrier();
        output.pop_front(1);
    }
}
