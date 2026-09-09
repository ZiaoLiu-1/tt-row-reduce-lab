#include "row_reduce/row_reduce.hpp"

#include <charconv>
#include <iostream>

namespace {
std::uint64_t parse_unsigned(std::string_view text) {
    std::uint64_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) {
        throw std::invalid_argument("expected a nonnegative integer");
    }
    return value;
}

bool run_case(row_reduce::Shape shape, std::string_view pattern, std::uint32_t seed,
              std::size_t nodes) {
    const auto input = row_reduce::prepare_input(shape, row_reduce::make_input(shape, pattern, seed), nodes);
    std::vector<float> rounded_reference;
    for (const auto& row : input.reference) {
        rounded_reference.push_back(row_reduce::bf16_to_float(
            row_reduce::float_to_bf16(static_cast<float>(row.sum))));
    }
    const auto exact = row_reduce::comparison_is_exact(pattern);
    const auto comparison = row_reduce::compare_rows(rounded_reference, input.reference, shape.cols, exact);
    std::cout << "{\"validation_level\":\"C0\",\"backend\":\"cpu_reference\","
              << "\"status\":\"" << (comparison.pass ? "pass" : "numerical-fail") << "\","
              << "\"rows\":" << shape.rows << ",\"cols\":" << shape.cols
              << ",\"padded_rows\":" << input.padded_rows << ",\"padded_cols\":" << input.padded_cols
              << ",\"pattern\":\"" << pattern << "\",\"seed\":" << seed
              << ",\"exact\":" << (exact ? "true" : "false")
              << ",\"quantized_fnv1a64\":\"" << row_reduce::quantized_fnv1a64(input.quantized)
              << "\",\"input_bytes_static\":" << input.tiled.size() * 2
              << ",\"output_bytes_static\":" << input.padded_rows * 32 * 2
              << ",\"comparison\":" << row_reduce::comparison_json(comparison) << "}\n";
    return comparison.pass;
}
} // namespace

int main(int argc, char** argv) {
    try {
        row_reduce::Shape shape{33, 33};
        std::string pattern = "decimals";
        std::uint32_t seed = 1;
        std::size_t nodes = 1;
        bool matrix = false;
        bool configured = false;
        for (int index = 1; index < argc; ++index) {
            const std::string_view argument(argv[index]);
            if (argument == "--help") {
                std::cout << "CPU-only reference; never executes Metal or ttsim.\n"
                          << "row_reduce_cpu [--rows M --cols N --pattern PATTERN --seed S --nodes 1]\n"
                          << "row_reduce_cpu --matrix\nPatterns:";
                for (const auto name : row_reduce::patterns) std::cout << ' ' << name;
                std::cout << '\n';
                return 0;
            }
            if (argument == "--matrix") {
                matrix = true;
                continue;
            }
            if (index + 1 >= argc) throw std::invalid_argument("option is missing its value");
            const std::string_view value(argv[++index]);
            configured = true;
            if (argument == "--pattern") {
                pattern = value;
            } else if (argument == "--rows") {
                shape.rows = parse_unsigned(value);
            } else if (argument == "--cols") {
                shape.cols = parse_unsigned(value);
            } else if (argument == "--nodes") {
                nodes = parse_unsigned(value);
            } else if (argument == "--seed") {
                const auto parsed = parse_unsigned(value);
                if (parsed > std::numeric_limits<std::uint32_t>::max()) {
                    throw std::invalid_argument("seed exceeds uint32 range");
                }
                seed = static_cast<std::uint32_t>(parsed);
            } else {
                throw std::invalid_argument("unknown option");
            }
        }
        if (matrix && configured) throw std::invalid_argument("--matrix cannot be combined with case options");
        bool pass = true;
        if (matrix) {
            for (const auto& test : row_reduce::required_cases) {
                pass = run_case(test.shape, test.pattern, test.seed, 1) && pass;
            }
        } else {
            pass = run_case(shape, pattern, seed, nodes);
        }
        return pass ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "input/error: " << error.what() << '\n';
        return 2;
    }
}
