# Source notes and kernel reading guide

All Metal API and kernel references below are pinned to Tenstorrent's
`tt-metal` commit `89e1256c982a5b4739d173bcc446c8c748a44b40`. These are
source-level checks; actual build, JIT and simulator outcomes belong in `STATE.md`
and `results/`. A readable reference kernel is not evidence that this project's
kernel executed.

## Attribution and scope

The Metal configuration in `src/metal_main.cpp` is a narrowed adaptation of
[test_reduce.cpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tests/tt_metal/tt_metal/llk/test_reduce.cpp).
The reader, compute and writer retain the essential algorithms and synchronization
of the three official kernels below. Their original copyright and Apache-2.0
notices remain in each derived file. The repository's `LICENSE` and `NOTICE`
record redistribution terms. The project does not claim the upstream LLK,
runtime, simulator, or reference kernel algorithm as original work.

Project additions are the bounded shape/value contract, independent CPU oracle
and layout index, cross-checks against upstream conversion, narrow CLI, complete
output-padding checks, raw readback records, changed-input reuse, poisoned output
buffers, source-bound runner and small named profiler scopes. Agent assistance
and the separate personal-understanding gate are recorded in
`docs/resume-evidence.md`.

| Upstream file | Use here | SHA-256 of consulted bytes |
| --- | --- | --- |
| [test_reduce.cpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tests/tt_metal/tt_metal/llk/test_reduce.cpp) | MeshTensor, named bindings, ProgramSpec, launch, FP32 configuration | `4434c3243b3c90e945ea495b1ddb3b76e4028e7a4d880e2a781f8b0571a01b65` |
| [reader_unary_8bank_2_0.cpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tests/tt_metal/tt_metal/test_kernels/dataflow/reader_unary_8bank_2_0.cpp) | Face-row scaler construction and per-tile asynchronous reads | `8fcd643dea361e580086de46270e5ba19630351d2da66260235cfb1bee960766` |
| [reduce_w.cpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tests/tt_metal/tt_metal/test_kernels/compute/reduce_w.cpp) | Width-tile reduction and register/DFB order | `45ec0b18db073bcb62e9e42484afe53807c1c9a174c0d810d015408d1bbe7516` |
| [writer_unary_8bank_2_0.cpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tests/tt_metal/tt_metal/test_kernels/dataflow/writer_unary_8bank_2_0.cpp) | Output wait/write/barrier/pop protocol | `d8f0d93976764f677d913b105aac5f356f71651dd1a2f311c8efab85783d7b78` |
| [reduce.h](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tt_metal/hw/inc/api/compute/reduce.h) | FP32 template argument, swapped operands, scaler and packer masks | `daae55eacc6c6d98ed9dea10c28223241472779dd9728017c4dd2a0c8ce3fea1` |
| [tilize_utils.cpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tt_metal/impl/data_format/tilize_utils.cpp) | Explicit uint16 tilize_nfaces/untilize_nfaces implementations used by host | `b77e4f413551247793b46e5bde01a81a07ed92cb8970e95e303214b07509f5ec` |
| [bfloat16.hpp](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tt_metal/api/tt-metalium/bfloat16.hpp) | Official RNE bit converter checked against independent quantizer | `7d6b140ef01fce02530a18836b66413d82f922dd3c19b6adaca5320f0e8a6c76` |

The compiler configuration also follows the pinned
[ComputeGen1Config](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tt_metal/api/tt-metalium/experimental/metal2_host_api/compute_hardware_config.hpp),
and profiler readback follows the pinned
[Mesh slow-dispatch example](https://github.com/tenstorrent/tt-metal/blob/89e1256c982a5b4739d173bcc446c8c748a44b40/tt_metal/programming_examples/profiler/test_custom_cycle_count_slow_dispatch/test_custom_cycle_count_slow_dispatch.cpp).

## Five code points to explain

1. **`transfer_words` / 上传前的布局转换**：输入是 host 的零 padding
   BF16 位模式；官方 `tilize_nfaces` 将其变成四个 16×16 face。
   每个结果必须与独立 CPU 索引完全相同，再把相邻两个 BF16 合成
   一个 UINT32 传输字。UINT32 TensorSpec 只描述原始传输页；DFB 的
   数据格式仍明确为 BF16。
2. **`make_program` / 三个角色的绑定**：一个 node 上有 reader、compute、
   writer。input 与 output DFB 各容纳两 tile，scaler 容纳一 tile；
   每个 DFB 只有一个 producer 和一个 consumer。`Ht`、`Wt`、`NC`
   是编译期参数，改变 shape 要重新专门化 program，不能只改 runtime args。
3. **`reader.cpp::kernel_main` / 数据什么时候有效**：先生成 scaler：
   四个 face 的第一行填 1，其余清零。每个输入 tile 先 reserve、发起
   NoC read、等待 barrier，最后 push；push 之后 compute 才能消费。
4. **`compute.cpp::kernel_main` / 一行跨多个 tile**：Wormhole 的行 SUM
   初始化前交换 scaler/data 格式配置。对一个 tile-row 的全部 `Wt`
   输入复用 Dest slot 0；整行累加完成后才 pack 成 BF16。host 的
   `enable_32_bit_dest=true` 与 `DST_ACCUM_MODE=1`、显式 `<..., true>`
   同时存在。保留参考实现的 register 获取、等待、pack、commit、release
   顺序与最后的 `reduce_uninit`。
5. **`run` / 真正结果与重复执行**：writer 等输出、写 DRAM、等待完成后
   pop。host `Finish` 后读取实际 DRAM，再 untilize 并取每行 column 0；
   其余列和补齐的行必须为零。每轮上传新输入并将输出填为 NaN，下一轮
   保留同一 mesh/tensor/workload，同时根据真实输入位选择不同输入。
   JSON 保留输入位和完整输出 tile 位；Python runner 独立复核。

## Profiler and numerical boundaries

`DeviceZoneScopedN` labels are `row_reduce_reader`, `row_reduce_compute` and
`row_reduce_writer`. They wrap kernel stages, never every tile. `--profile`
requires `TT_METAL_DEVICE_PROFILER=1`; the host calls
`ReadMeshDeviceProfilerResults` after synchronization. Missing CSV is a failure
to obtain profiler evidence, not an empty successful measurement.

`execute_wall_seconds` spans host enqueue (including first-run JIT) through
`Finish`, excluding transfers. With ttsim this is simulator wall time. Raw
profiler counters and simulator time do not establish silicon latency,
bandwidth or speedup. The numeric acceptance expression remains the predeclared
project contract; tests must fail rather than widen it after observing results.
