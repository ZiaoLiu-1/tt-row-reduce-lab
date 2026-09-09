// SPDX-FileCopyrightText: © 2023 Tenstorrent USA, Inc.
// SPDX-FileCopyrightText: © 2026 Ziao Liu
// SPDX-License-Identifier: Apache-2.0
// The Metal 2.0 launch configuration is adapted from the pinned test_reduce.cpp.
// Project-specific contract, input validation, output checks and CLI are described
// in docs/source-notes.md. No CPU fallback computes the device's actual output.

#include "row_reduce/row_reduce.hpp"

#include <tt-metalium/bfloat16.hpp>
#include <tt-metalium/distributed.hpp>
#include <tt-metalium/experimental/metal2_host_api/program.hpp>
#include <tt-metalium/host_api.hpp>
#include <tt-metalium/tensor/mesh_tensor.hpp>
#include <tt-metalium/tilize_utils.hpp>
#include <tt-metalium/tt_metal.hpp>
#include <umd/device/types/arch.hpp>

#include <algorithm>
#include <bit>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace metal = tt::tt_metal;
namespace ex = tt::tt_metal::experimental;
namespace distributed = tt::tt_metal::distributed;
namespace rr = row_reduce;

namespace {
constexpr std::uint32_t tile_bytes = 32 * 32 * sizeof(std::uint16_t);
const ex::NodeCoord node{0, 0};
const ex::DFBSpecName input_dfb{"input_dfb"};
const ex::DFBSpecName scaler_dfb{"scaler_dfb"};
const ex::DFBSpecName output_dfb{"output_dfb"};
const ex::KernelSpecName reader_name{"reader"};
const ex::KernelSpecName compute_name{"compute"};
const ex::KernelSpecName writer_name{"writer"};
const ex::TensorParamName input_name{"input_tensor"};
const ex::TensorParamName output_name{"output_tensor"};

struct Options {
    rr::Shape shape{32, 32};
    std::string pattern = "random";
    std::uint32_t seed = 1;
    std::size_t repeats = 1;
    std::size_t nodes = 1;
    std::string input_file;
    bool profile = false;
};

std::size_t integer(std::string_view text, std::string_view flag) {
    std::size_t result{};
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        throw std::invalid_argument(std::string(flag) + " requires an unsigned integer");
    }
    return result;
}

Options parse_options(int argc, char** argv) {
    Options result;
    for (int i = 1; i < argc; ++i) {
        const std::string_view flag(argv[i]);
        if (flag == "--profile") {
            result.profile = true;
            continue;
        }
        if (i + 1 == argc) {
            throw std::invalid_argument("Missing value for " + std::string(flag));
        }
        const std::string_view value(argv[++i]);
        if (flag == "--rows") result.shape.rows = integer(value, flag);
        else if (flag == "--cols") result.shape.cols = integer(value, flag);
        else if (flag == "--nodes") result.nodes = integer(value, flag);
        else if (flag == "--repeat") result.repeats = integer(value, flag);
        else if (flag == "--pattern") result.pattern = value;
        else if (flag == "--input-file") result.input_file = value;
        else if (flag == "--seed") {
            const auto seed = integer(value, flag);
            if (seed > std::numeric_limits<std::uint32_t>::max()) {
                throw std::invalid_argument("Seed exceeds uint32");
            }
            result.seed = static_cast<std::uint32_t>(seed);
        } else throw std::invalid_argument("Unknown option " + std::string(flag));
    }
    if (result.repeats == 0 || result.repeats > 100) {
        throw std::invalid_argument("Repeat must be in [1,100]");
    }
    if (result.pattern == "zeros") result.pattern = "zero";
    if (result.pattern == "fractions") result.pattern = "decimals";
    return result;
}

std::vector<float> read_input_file(const std::string& path) {
    std::ifstream stream(path);
    if (!stream) throw std::invalid_argument("Cannot open input file");
    std::vector<float> values;
    std::string token;
    while (stream >> token) {
        float value{};
        const auto parsed = std::from_chars(token.data(), token.data() + token.size(), value);
        if (parsed.ec != std::errc{} || parsed.ptr != token.data() + token.size()) {
            throw std::invalid_argument("Input file contains an invalid float token");
        }
        values.push_back(value);
        if (values.size() > 128 * 128) throw std::invalid_argument("Input file exceeds maximum host length");
    }
    if (!stream.eof()) throw std::invalid_argument("Failed while reading input file");
    return values;
}

std::string json_string(std::string_view value) {
    std::string output = "\"";
    constexpr char hex[] = "0123456789abcdef";
    for (const unsigned char c : value) {
        if (c == '\\' || c == '"') { output += '\\'; output += static_cast<char>(c); }
        else if (c < 32) { output += "\\u00"; output += hex[c >> 4]; output += hex[c & 15]; }
        else output += static_cast<char>(c);
    }
    return output + '"';
}

void json_number(double value) {
    if (std::isfinite(value)) std::cout << std::setprecision(17) << value;
    else std::cout << "null";
}

// UINT32 describes raw transfer words, not the logical tensor's BF16 datatype.
// Each interleaved DRAM page holds one physical 32x32 BF16 face-layout tile.
metal::TensorSpec flat_tensor_spec(std::uint32_t pages) {
    const auto page = metal::PageConfig(metal::Layout::ROW_MAJOR);
    const auto memory = metal::MemoryConfig{metal::TensorMemoryLayout::INTERLEAVED, metal::BufferType::DRAM};
    const auto layout = metal::TensorLayout(metal::DataType::UINT32, page, memory);
    return metal::TensorSpec(metal::Shape{pages, tile_bytes / sizeof(std::uint32_t)}, layout);
}

metal::Program make_program(distributed::MeshDevice& device, const metal::MeshTensor& input,
                            const metal::MeshTensor& output, std::uint32_t ht, std::uint32_t wt) {
    const ex::DataflowBufferSpec input_spec{
        .unique_id = input_dfb, .entry_size = tile_bytes, .num_entries = 2,
        .data_format_metadata = tt::DataFormat::Float16_b, .tile_format_metadata = metal::Tile({32, 32})};
    const ex::DataflowBufferSpec scaler_spec{
        .unique_id = scaler_dfb, .entry_size = tile_bytes, .num_entries = 1,
        .data_format_metadata = tt::DataFormat::Float16_b, .tile_format_metadata = metal::Tile({32, 32})};
    const ex::DataflowBufferSpec output_spec{
        .unique_id = output_dfb, .entry_size = tile_bytes, .num_entries = 2,
        .data_format_metadata = tt::DataFormat::Float16_b, .tile_format_metadata = metal::Tile({32, 32})};
    const ex::KernelSpec reader{
        .unique_id = reader_name,
        .source = std::filesystem::path(RR_KERNEL_DIR) / "reader.cpp",
        .num_threads = 1,
        .dfb_bindings = {ex::ProducerOf(input_dfb, "out_data"), ex::ProducerOf(scaler_dfb, "out_scaler")},
        .tensor_bindings = {{.tensor_parameter_name = input_name, .accessor_name = "src_tensor"}},
        .runtime_arg_schema = {.runtime_arg_names = {"num_tiles", "scaler"}},
        .hw_config = ex::DataMovementHardwareConfig{ex::DataMovementGen1Config{
            .processor = metal::DataMovementProcessor::RISCV_1, .noc = metal::NOC::RISCV_1_default}}};
    const ex::KernelSpec writer{
        .unique_id = writer_name,
        .source = std::filesystem::path(RR_KERNEL_DIR) / "writer.cpp",
        .num_threads = 1,
        .dfb_bindings = {ex::ConsumerOf(output_dfb, "in")},
        .tensor_bindings = {{.tensor_parameter_name = output_name, .accessor_name = "dst_tensor"}},
        .runtime_arg_schema = {.runtime_arg_names = {"num_tiles"}},
        .hw_config = ex::DataMovementHardwareConfig{ex::DataMovementGen1Config{
            .processor = metal::DataMovementProcessor::RISCV_0, .noc = metal::NOC::RISCV_0_default}}};
    const ex::KernelSpec compute{
        .unique_id = compute_name,
        .source = std::filesystem::path(RR_KERNEL_DIR) / "compute.cpp",
        .num_threads = 1,
        .compiler_options = {.defines = {{"REDUCE_DIM", "ReduceDim::REDUCE_ROW"}, {"REDUCE_OP", "PoolType::SUM"},
                                        {"MATH_ONLY", "0"}, {"DST_ACCUM_MODE", "1"}}},
        .dfb_bindings = {ex::ConsumerOf(input_dfb, "in_data"), ex::ConsumerOf(scaler_dfb, "in_scaler"),
                         ex::ProducerOf(output_dfb, "out")},
        // These are compile-time specializations, never changed by runtime args.
        .compile_time_args = {{"Ht", ht}, {"Wt", wt}, {"NC", 1u}},
        .hw_config = ex::ComputeGen1Config{.fpu_math_fidelity = metal::MathFidelity::HiFi4,
                                         .enable_32_bit_dest = true, .double_buffer_dest = true}};
    const ex::WorkUnitSpec work_unit{.name = "row_reduce", .kernels = {reader_name, writer_name, compute_name},
                                    .target_nodes = node};
    const ex::ProgramSpec spec{
        .name = "tt_row_reduce_single_node",
        .kernels = {reader, writer, compute},
        .dataflow_buffers = {input_spec, scaler_spec, output_spec},
        .tensor_parameters = {{.unique_id = input_name, .spec = input.tensor_spec()},
                              {.unique_id = output_name, .spec = output.tensor_spec()}},
        .work_units = {work_unit}};
    return ex::MakeProgramFromSpec(device, spec);
}

void bind_arguments(metal::Program& program, const metal::MeshTensor& input,
                    const metal::MeshTensor& output, std::uint32_t ht, std::uint32_t wt) {
    ex::ProgramRunArgs args;
    args.kernel_run_args = {
        ex::ProgramRunArgs::KernelRunArgs{.kernel = reader_name,
            .runtime_arg_values = ex::MakeRuntimeArgsForSingleNode(
                node, {{"num_tiles", ht * wt}, {"scaler", std::bit_cast<std::uint32_t>(1.0f)}})},
        ex::ProgramRunArgs::KernelRunArgs{.kernel = writer_name,
            .runtime_arg_values = ex::MakeRuntimeArgsForSingleNode(node, {{"num_tiles", ht}})},
        ex::ProgramRunArgs::KernelRunArgs{.kernel = compute_name}};
    args.tensor_args = {{input_name, ex::ProgramRunArgs::TensorArgument{input}},
                        {output_name, ex::ProgramRunArgs::TensorArgument{output}}};
    ex::SetProgramRunArgs(program, args);
}

std::vector<std::uint32_t> transfer_words(const rr::PreparedInput& input) {
    const auto tiled = tilize_nfaces(input.padded_row_major, static_cast<std::uint32_t>(input.padded_rows),
                                    static_cast<std::uint32_t>(input.padded_cols));
    if (tiled != input.tiled) throw std::runtime_error("Upstream tilizer disagrees with independent layout oracle");
    std::vector<std::uint32_t> words(tiled.size() / 2);
    for (std::size_t i = 0; i < words.size(); ++i) {
        words[i] = tiled[2 * i] | (static_cast<std::uint32_t>(tiled[2 * i + 1]) << 16);
    }
    return words;
}

void check_upstream_quantization(const std::vector<float>& original, const rr::PreparedInput& input) {
    for (std::size_t i = 0; i < original.size(); ++i) {
        if (fp32_to_bf16_bits_round_to_nearest_even(original[i]) != input.quantized[i]) {
            throw std::runtime_error("Upstream BF16 converter disagrees with independent quantizer");
        }
    }
}

template<class Field>
void print_row_array(const rr::Comparison& result, Field field) {
    std::cout << '[';
    for (std::size_t i = 0; i < result.rows.size(); ++i) {
        if (i != 0) std::cout << ',';
        json_number(field(result.rows[i]));
    }
    std::cout << ']';
}

void emit_result(const Options& options, std::string_view pattern, std::size_t iteration,
                 const rr::PreparedInput& input, const rr::Comparison& comparison, bool padding_zero,
                 const std::vector<std::uint16_t>& output_bits, double seconds) {
    const bool simulator = std::getenv("TT_METAL_SIMULATOR") != nullptr;
    const bool pass = comparison.pass && padding_zero;
    std::cout << "TT_ROW_REDUCE_RESULT={\"status\":" << json_string(pass ? "pass" : "numerical-fail")
              << ",\"validation_stage\":" << json_string(simulator ? "C3" : "C4")
              << ",\"backend\":" << json_string(simulator ? "ttsim" : "device")
              << ",\"upstream_commit\":" << json_string(RR_UPSTREAM_COMMIT)
              << ",\"compiled_source_commit\":" << json_string(RR_SOURCE_COMMIT)
              << ",\"rows\":" << options.shape.rows << ",\"cols\":" << options.shape.cols
              << ",\"padded_rows\":" << input.padded_rows << ",\"padded_cols\":" << input.padded_cols
              << ",\"nodes\":1,\"pattern\":" << json_string(pattern) << ",\"seed\":" << options.seed
              << ",\"repeat_index\":" << iteration << ",\"repeat_count\":" << options.repeats
              << ",\"exact\":" << (pattern != "file" && rr::comparison_is_exact(pattern) ? "true" : "false")
              << ",\"padding_zero\":" << (padding_zero ? "true" : "false")
              << ",\"input_layout_verified_against_upstream\":true,\"input_quantization_verified_against_upstream\":true"
              << ",\"quantized_bits_encoding\":\"logical_row_major_bf16_little_endian\""
              << ",\"quantized_bits_hex\":" << json_string(rr::quantized_bits_hex(input.quantized))
              << ",\"output_bits_encoding\":\"tile_face_bf16_little_endian\""
              << ",\"output_tiled_bits_hex\":" << json_string(rr::quantized_bits_hex(output_bits))
              << ",\"actual\":";
    print_row_array(comparison, [](const auto& row) { return row.actual; });
    std::cout << ",\"reference\":";
    print_row_array(comparison, [](const auto& row) { return row.reference; });
    std::cout << ",\"abs_errors\":";
    print_row_array(comparison, [](const auto& row) { return row.abs_error; });
    std::cout << ",\"tolerances\":";
    print_row_array(comparison, [](const auto& row) { return row.tolerance; });
    std::cout << ",\"max_abs_error\":";
    json_number(comparison.max_abs_error);
    std::cout << ",\"max_scaled_error\":";
    json_number(comparison.max_scaled_error);
    std::cout << ",\"failed_rows\":[";
    bool first = true;
    for (std::size_t i = 0; i < comparison.rows.size(); ++i) {
        if (!comparison.rows[i].pass) {
            if (!first) std::cout << ',';
            std::cout << i;
            first = false;
        }
    }
    std::cout << "],\"execute_wall_seconds\":";
    json_number(seconds);
    std::cout << ",\"measurement_kind\":" << json_string(simulator ? "simulator_wall_time" : "host_wall_time")
              << ",\"timing_scope\":\"enqueue_including_first_run_JIT_and_Finish_excluding_transfers\"}\n" << std::flush;
}

bool run(const Options& options, rr::PreparedInput prepared, const std::shared_ptr<distributed::MeshDevice>& device) {
    if (device->arch() != tt::ARCH::WORMHOLE_B0) throw std::runtime_error("Only Wormhole is supported");
    const auto ht = static_cast<std::uint32_t>(prepared.padded_rows / 32);
    const auto wt = static_cast<std::uint32_t>(prepared.padded_cols / 32);
    auto input = metal::MeshTensor::allocate_on_device(*device, flat_tensor_spec(ht * wt));
    auto output = metal::MeshTensor::allocate_on_device(*device, flat_tensor_spec(ht));
    distributed::MeshWorkload workload;
    const auto coord = distributed::MeshCoordinate(0, 0);
    const auto range = distributed::MeshCoordinateRange(coord, coord);
    workload.add_program(range, make_program(*device, input, output, ht, wt));
    auto& program = workload.get_programs().at(range);
    auto& queue = device->mesh_command_queue();
    bool pass = true;
    std::string pattern = options.input_file.empty() ? options.pattern : "file";
    for (std::size_t iteration = 0; iteration < options.repeats; ++iteration) {
        if (iteration != 0) {
            // Inspect the actual bits, so even a one-element random input or an
            // input file containing only ones must change on the next launch.
            const bool already_ones = std::all_of(prepared.quantized.begin(), prepared.quantized.end(),
                                                  [](std::uint16_t bits) { return bits == 0x3f80u; });
            pattern = already_ones ? "zero" : "ones";
            const auto original = rr::make_input(options.shape, pattern, options.seed);
            prepared = rr::prepare_input(options.shape, original, options.nodes);
            check_upstream_quantization(original, prepared);
        }
        bind_arguments(program, input, output, ht, wt);
        const auto words = transfer_words(prepared);
        metal::detail::WriteToBuffer(*input.mesh_buffer().get_reference_buffer(), words);
        // Poison every output word first, so skipped writes cannot accidentally
        // pass an all-zero case or inherit a previous invocation's allocation.
        const std::vector<std::uint32_t> poison(ht * tile_bytes / sizeof(std::uint32_t), 0x7fc17fc1u);
        metal::detail::WriteToBuffer(*output.mesh_buffer().get_reference_buffer(), poison);
        const auto begin = std::chrono::steady_clock::now();
        distributed::EnqueueMeshWorkload(queue, workload, false);
        distributed::Finish(queue);
        const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - begin).count();
        std::vector<std::uint32_t> result_words;
        metal::detail::ReadFromBuffer(*output.mesh_buffer().get_reference_buffer(), result_words);
        if (result_words.size() != poison.size()) throw std::runtime_error("Output transfer has the wrong length");
        std::vector<std::uint16_t> result_bits(result_words.size() * 2);
        for (std::size_t i = 0; i < result_words.size(); ++i) {
            result_bits[2 * i] = static_cast<std::uint16_t>(result_words[i]);
            result_bits[2 * i + 1] = static_cast<std::uint16_t>(result_words[i] >> 16);
        }
        const auto row_major = untilize_nfaces(result_bits, static_cast<std::uint32_t>(prepared.padded_rows), 32);
        bool padding_zero = true;
        for (std::size_t r = 0; r < prepared.padded_rows; ++r) {
            for (std::size_t c = 0; c < 32; ++c) {
                if ((r >= options.shape.rows || c != 0) && (row_major[r * 32 + c] & 0x7fffu) != 0) padding_zero = false;
            }
        }
        std::vector<float> actual(options.shape.rows);
        for (std::size_t r = 0; r < actual.size(); ++r) actual[r] = rr::bf16_to_float(row_major[r * 32]);
        const bool exact = pattern != "file" && rr::comparison_is_exact(pattern);
        const auto comparison = rr::compare_rows(actual, prepared.reference, options.shape.cols, exact);
        if (comparison.pass && padding_zero && rr::decode_output(result_bits, options.shape) != actual) {
            throw std::runtime_error("Upstream untilizer disagrees with independent output decoder");
        }
        emit_result(options, pattern, iteration, prepared, comparison, padding_zero, result_bits, seconds);
        pass = comparison.pass && padding_zero && pass;
        if (options.profile) metal::ReadMeshDeviceProfilerResults(*device);
    }
    return pass;
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        std::cout << "tt_row_reduce_metal --rows M --cols N --pattern zero|one_hot|ones|cancellation|small_integers|decimals|random "
                     "[--seed U32] [--nodes 1] [--repeat 1..100] [--input-file FLOAT_TEXT] [--profile]\n";
        return 0;
    }
    std::shared_ptr<distributed::MeshDevice> device;
    bool before_device = true;
    try {
        const auto options = parse_options(argc, argv);
        const auto original = options.input_file.empty() ? rr::make_input(options.shape, options.pattern, options.seed)
                                                         : read_input_file(options.input_file);
        // All shape/value/length/node checks and the independent host checks run
        // before device creation, allocation, or any upload.
        auto prepared = rr::prepare_input(options.shape, original, options.nodes);
        check_upstream_quantization(original, prepared);
        (void)transfer_words(prepared);
        if (options.profile) {
            const char* enabled = std::getenv("TT_METAL_DEVICE_PROFILER");
            if (enabled == nullptr || std::string_view(enabled) != "1") {
                throw std::invalid_argument("--profile requires TT_METAL_DEVICE_PROFILER=1 before startup");
            }
        }
        before_device = false;
        device = distributed::MeshDevice::create_unit_mesh(0);
        const bool pass = run(options, std::move(prepared), device);
        const bool closed = device->close();
        device.reset();
        return pass && closed ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "TT_ROW_REDUCE_ERROR={\"status\":" << json_string(before_device ? "invalid-input" : "runtime-error")
                  << ",\"before_device\":" << (before_device ? "true" : "false")
                  << ",\"message\":" << json_string(error.what()) << "}\n";
        if (device) {
            try { device->close(); } catch (...) { /* Preserve the original failure. */ }
        }
        return before_device ? 2 : 1;
    }
}
