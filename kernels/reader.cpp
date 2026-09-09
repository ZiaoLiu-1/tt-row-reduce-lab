// SPDX-FileCopyrightText: © 2023 Tenstorrent USA, Inc.
// SPDX-FileCopyrightText: © 2026 Ziao Liu
// SPDX-License-Identifier: Apache-2.0
// Adapted from the pinned reader_unary_8bank_2_0.cpp; see docs/source-notes.md.

#include <cstdint>
#include "api/dataflow/dataflow_api.h"
#include "api/dataflow/dataflow_buffer.h"
#include "api/dataflow/noc.h"
#include "experimental/kernel_args.h"
#include "tools/profiler/kernel_profiler.hpp"

void kernel_main() {
    DeviceZoneScopedN("row_reduce_reader");
    DataflowBuffer scaler(dfb::out_scaler);
    scaler.reserve_back(1);
    // The LLK reduce path consumes the first row of EACH 16x16 face.
    // This is deliberately not a dense all-ones 32x32 row-major tile.
    auto* values = reinterpret_cast<uint16_t*>(scaler.get_write_ptr());
    for (uint32_t i = 0; i < 1024; ++i) {
        values[i] = 0;
    }
    const uint16_t scale_bits = static_cast<uint16_t>(get_arg(args::scaler) >> 16);
    for (uint32_t face = 0; face < 4; ++face) {
        for (uint32_t col = 0; col < 16; ++col) {
            values[face * 256 + col] = scale_bits;
        }
    }
    scaler.push_back(1);

    Noc noc;
    DataflowBuffer input(dfb::out_data);
    const auto source = TensorAccessor(tensor::src_tensor);
    const uint32_t tile_bytes = input.get_entry_size();
    const uint32_t num_tiles = get_arg(args::num_tiles);
    // One tile per transaction: every tile-row arrives width-contiguously.
    for (uint32_t tile = 0; tile < num_tiles; ++tile) {
        input.reserve_back(1);
        noc.async_read(source, input, tile_bytes, {.page_id = tile}, {});
        noc.async_read_barrier();
        input.push_back(1);
    }
}
