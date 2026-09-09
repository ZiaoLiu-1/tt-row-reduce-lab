#include "row_reduce/row_reduce.hpp"

#include <functional>
#include <iostream>
#include <numeric>

namespace {
using namespace row_reduce;
std::size_t assertions = 0;

void check(bool condition, std::string_view explanation) {
    ++assertions;
    if (!condition) throw std::runtime_error(std::string(explanation));
}

template <class Exception = std::exception, class Function>
void rejects(Function&& function, std::string_view explanation) {
    bool rejected = false;
    try {
        function();
    } catch (const Exception&) {
        rejected = true;
    }
    check(rejected, explanation);
}

std::vector<float> rounded_reference(const PreparedInput& input) {
    std::vector<float> output;
    for (const auto& row : input.reference) {
        output.push_back(bf16_to_float(float_to_bf16(static_cast<float>(row.sum))));
    }
    return output;
}

void bf16_known_answers() {
    check(float_to_bf16(0.0F) == 0x0000, "positive zero");
    check(float_to_bf16(-0.0F) == 0x8000, "negative zero");
    check(float_to_bf16(1.0F) == 0x3f80, "one");
    check(float_to_bf16(-1.0F) == 0xbf80, "minus one");
    check(float_to_bf16(0.1F) == 0x3dcd, "0.1 quantization");
    check(float_to_bf16(-0.2F) == 0xbe4d, "-0.2 quantization");
    check(float_to_bf16(0.3F) == 0x3e9a, "0.3 quantization");
    check(bf16_to_float(0x3dcd) == 0.10009765625F, "decode 0.1");
    check(bf16_to_float(0xbe4d) == -0.2001953125F, "decode -0.2");
    check(bf16_to_float(0x3e9a) == 0.30078125F, "decode 0.3");
    const float even_tie = 1.0F + 0x1p-8F;
    const float odd_tie = 1.0F + 3.0F * 0x1p-8F;
    check(float_to_bf16(even_tie) == 0x3f80, "even tie rounds downward");
    check(float_to_bf16(odd_tie) == 0x3f82, "odd tie rounds upward");
    check(float_to_bf16(-even_tie) == 0xbf80, "negative even tie");
    check(float_to_bf16(-odd_tie) == 0xbf82, "negative odd tie");
    check(float_to_bf16(std::nextafter(even_tie, 0.0F)) == 0x3f80, "below midpoint");
    check(float_to_bf16(std::nextafter(even_tie, 2.0F)) == 0x3f81, "above midpoint");
    check(std::isinf(bf16_to_float(float_to_bf16(std::numeric_limits<float>::infinity()))), "infinity stays infinity");
    check(std::isnan(bf16_to_float(float_to_bf16(std::bit_cast<float>(0x7f800001U)))), "low-payload NaN cannot become infinity");
    for (std::uint32_t bits = 0; bits <= 0xffffU; ++bits) {
        const auto original = static_cast<std::uint16_t>(bits);
        const auto decoded = bf16_to_float(original);
        if (std::isfinite(decoded)) {
            check(float_to_bf16(decoded) == original, "all finite BF16 values round-trip exactly");
        }
    }
}

void shape_and_length_rejection() {
    for (const auto shape : std::array<Shape, 6>{{{0, 1}, {1, 0}, {0, 0}, {129, 1}, {1, 129}, {129, 129}}}) {
        rejects<std::invalid_argument>([&] { validate_shape(shape); }, "unsupported shape rejected");
    }
    rejects<std::overflow_error>([] { validate_shape({std::numeric_limits<std::size_t>::max(), 2}); }, "overflow diagnosed before allocation");
    for (const auto nodes : {0U, 2U, 4U, 99U}) {
        rejects<std::invalid_argument>([&] { validate_shape({32, 32}, nodes); }, "invalid nodes rejected");
    }
    rejects<std::invalid_argument>([] { prepare_input({1, 2}, std::array<float, 1>{1.0F}); }, "short host vector rejected");
    rejects<std::invalid_argument>([] { prepare_input({1, 1}, std::array<float, 2>{1.0F, 1.0F}); }, "long host vector rejected");
    rejects<std::invalid_argument>([] { prepare_input({1, 1}, {}); }, "empty host vector rejected");
    rejects<std::invalid_argument>([] { padded_dimension(0); }, "zero padding dimension rejected");
    rejects<std::invalid_argument>([] { padded_dimension(129); }, "oversized padding dimension rejected");
    for (const auto dimension : {1U, 31U, 32U, 33U, 65U, 97U, 128U}) {
        const auto padded = padded_dimension(dimension);
        check(padded % 32 == 0 && padded >= dimension && padded - dimension < 32, "minimal whole tile padding");
    }
}

void values_rejection() {
    for (const auto bad : {0x1p-9F, -0x1p-9F, 1.01F, -2.0F, std::numeric_limits<float>::max(),
                           std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(),
                           std::numeric_limits<float>::quiet_NaN()}) {
        rejects<std::invalid_argument>([&] { prepare_input({1, 1}, std::array<float, 1>{bad}); }, "invalid value rejected");
    }
    for (const auto good : {0.0F, -0.0F, 0x1p-8F, -0x1p-8F, 1.0F, -1.0F, 1.001F}) {
        check(prepare_input({1, 1}, std::array<float, 1>{good}).quantized.size() == 1, "range is checked after quantization");
    }
    rejects<std::invalid_argument>([] { make_input({1, 1}, "unknown"); }, "unknown pattern rejected");
    rejects<std::invalid_argument>([] { comparison_is_exact("unknown"); }, "unknown comparison pattern rejected");
}

void face_known_coordinates() {
    struct Anchor { std::size_t row, col, index; };
    constexpr std::array<Anchor, 17> anchors{{
        {0, 0, 0}, {0, 15, 15}, {15, 0, 240}, {15, 15, 255},
        {0, 16, 256}, {15, 31, 511}, {16, 0, 512}, {31, 15, 767},
        {16, 16, 768}, {31, 31, 1023}, {0, 32, 1024}, {31, 63, 2047},
        {32, 0, 2048}, {32, 16, 2304}, {32, 32, 3072}, {63, 63, 4095}, {17, 18, 786},
    }};
    for (const auto anchor : anchors) {
        check(tile_face_index(anchor.row, anchor.col, 64, 64) == anchor.index, "explicit face/tile anchor");
    }
    rejects<std::out_of_range>([] { tile_face_index(64, 0, 64, 64); }, "out of range row");
    rejects<std::out_of_range>([] { tile_face_index(0, 64, 64, 64); }, "out of range col");
    rejects<std::invalid_argument>([] { tile_face_index(0, 0, 31, 32); }, "non-tiled physical dimensions");
    rejects<std::invalid_argument>([] { tilize<int>({}, 32, 32); }, "tilize missing data");
    rejects<std::invalid_argument>([] { untilize<int>({}, 32, 32); }, "untilize missing data");
}

void face_independent_enumeration() {
    // Independent expected ordering enumerates tile, face, face row, face column.
    // This catches a mutually consistent but wrong tilize/untilize index formula.
    constexpr std::array<std::size_t, 4> face_row{0, 0, 16, 16};
    constexpr std::array<std::size_t, 4> face_col{0, 16, 0, 16};
    for (const auto shape : std::array<Shape, 4>{{{32, 32}, {64, 96}, {128, 64}, {128, 128}}}) {
        std::vector<std::uint32_t> row_major(shape.rows * shape.cols);
        std::iota(row_major.begin(), row_major.end(), 0U);
        const auto tiled = tilize<std::uint32_t>(row_major, shape.rows, shape.cols);
        std::size_t physical = 0;
        for (std::size_t tile_row = 0; tile_row < shape.rows; tile_row += 32) {
            for (std::size_t tile_col = 0; tile_col < shape.cols; tile_col += 32) {
                for (std::size_t face = 0; face < 4; ++face) {
                    for (std::size_t r = 0; r < 16; ++r) {
                        for (std::size_t c = 0; c < 16; ++c) {
                            const auto expected = (tile_row + face_row[face] + r) * shape.cols +
                                                   tile_col + face_col[face] + c;
                            check(tiled[physical++] == expected, "independent physical position expected value");
                        }
                    }
                }
            }
        }
        check(physical == tiled.size(), "enumeration visits full buffer");
        check(untilize<std::uint32_t>(tiled, shape.rows, shape.cols) == row_major, "tagged layout round-trip");
    }
}

void quantized_oracle_known_answer() {
    const std::array<float, 6> values{0.1F, -0.2F, 0.3F, -1.0F, 0.5F, 0x1p-8F};
    const auto prepared = prepare_input({2, 3}, values);
    check(prepared.reference[0].sum == 0.20068359375, "double oracle sums uploaded quantized bits");
    check(prepared.reference[0].abs_sum == 0.60107421875, "quantized absolute sum");
    check(prepared.reference[1].sum == -0.49609375, "exact second row sum");
    check(prepared.reference[1].abs_sum == 1.50390625, "exact second row magnitude");
    const double unquantized = static_cast<double>(values[0]) + values[1] + values[2];
    check(prepared.reference[0].sum != unquantized, "unquantized float golden is distinguishable");
    check(prepared.quantized == std::vector<std::uint16_t>({0x3dcd, 0xbe4d, 0x3e9a, 0xbf80, 0x3f00, 0x3b80}), "known uploaded bits");
}

void all_shapes_all_patterns() {
    std::size_t cases = 0;
    for (const auto& test : required_cases) {
        for (const auto pattern : patterns) {
            const auto values = make_input(test.shape, pattern, test.seed);
            const auto input = prepare_input(test.shape, values);
            check(untilize<std::uint16_t>(input.tiled, input.padded_rows, input.padded_cols) == input.padded_row_major,
                  "all shapes BF16 layout round-trip");
            for (std::size_t row = 0; row < input.padded_rows; ++row) {
                for (std::size_t col = 0; col < input.padded_cols; ++col) {
                    if (row >= test.shape.rows || col >= test.shape.cols) {
                        check(input.padded_row_major[row * input.padded_cols + col] == 0x0000,
                              "all host padding is positive zero");
                    }
                }
            }
            for (std::size_t row = 0; row < test.shape.rows; ++row) {
                double expected = 0;
                if (pattern == "one_hot") expected = (row + 1 == test.shape.rows) ? 1.0 : 0.0;
                if (pattern == "ones") expected = static_cast<double>(test.shape.cols);
                if (pattern == "small_integers") expected = static_cast<double>(std::min(row % 17 + 1, test.shape.cols));
                if (comparison_is_exact(pattern)) {
                    check(input.reference[row].sum == expected, "diagnostic sums match independent analytic answers");
                }
            }
            const auto actual = rounded_reference(input);
            check(compare_rows(actual, input.reference, test.shape.cols, comparison_is_exact(pattern)).pass,
                  "BF16-rounded quantized oracle fits prior budget");
            ++cases;
        }
    }
    check(cases == 84, "12 shapes times 7 CPU patterns");
}

void deterministic_generation() {
    const auto a = make_input({33, 33}, "random", 123);
    const auto b = make_input({33, 33}, "random", 123);
    const auto c = make_input({33, 33}, "random", 124);
    check(a == b, "fixed seed exact reproducibility");
    check(a != c, "changed seed changes input");
    const auto quantized = prepare_input({33, 33}, a);
    for (const auto bits : quantized.quantized) {
        const auto magnitude = std::abs(bf16_to_float(bits));
        check(magnitude >= 0x1p-8F && magnitude <= 1.0F, "random generation respects finite range");
    }
    check(quantized_bits_hex(std::array<std::uint16_t, 3>{0x3f80, 0xbf80, 0}) == "803f80bf0000", "BF16 hex is explicit little-endian bytes");
    check(quantized_fnv1a64({}) == "cbf29ce484222325", "empty FNV known answer");
    check(quantized_fnv1a64(std::array<std::uint16_t, 1>{0}) == "08328807b4eb6fed", "two zero bytes FNV known answer");
}

void output_extract_and_padding() {
    const Shape shape{33, 33};
    // Populate serialized output without tilize/index helper: row 0..15 values
    // occur at face-0 offsets 0,16,...; row 16..31 at face-2 offsets 512,528,...
    std::vector<std::uint16_t> tiled(2048, 0);
    for (std::size_t row = 0; row < 16; ++row) tiled[row * 16] = float_to_bf16(static_cast<float>(row + 1));
    for (std::size_t row = 16; row < 32; ++row) tiled[512 + (row - 16) * 16] = float_to_bf16(static_cast<float>(row + 1));
    tiled[1024] = float_to_bf16(33.0F);
    const auto actual = decode_output(tiled, shape);
    for (std::size_t row = 0; row < 33; ++row) check(actual[row] == static_cast<float>(row + 1), "extract row at column zero");
    check(bf16_to_float(tiled[1]) != actual[1], "first M physical elements are not row results");
    auto bad_column = tiled;
    bad_column[256] = 0x3f80;
    rejects<std::runtime_error>([&] { decode_output(bad_column, shape); }, "output nonzero non-result column rejected");
    auto bad_row = tiled;
    bad_row[1040] = 0x3f80;
    rejects<std::runtime_error>([&] { decode_output(bad_row, shape); }, "padded output row rejected");
    auto bad_finite = tiled;
    bad_finite[1] = 0x7fc0;
    rejects<std::runtime_error>([&] { decode_output(bad_finite, shape); }, "nonfinite output padding rejected");
    rejects<std::invalid_argument>([&] { decode_output(std::span(tiled).first(1024), shape); }, "truncated output rejected");
    auto negative_zero = tiled;
    negative_zero[1] = 0x8000;
    check(decode_output(negative_zero, shape) == actual, "zero sign is irrelevant for output");
}

void numeric_budget_and_exact_path() {
    const RowReference reference{1.0, 1.0};
    const double independent_gamma = (65.0 / 16777216.0) / (1.0 - 65.0 / 16777216.0);
    const double expected = 1.0 / 256.0 + (257.0 / 256.0) * independent_gamma + 1.0 / 1048576.0;
    check(error_budget(reference, 1) == expected, "budget matches prior mathematical expression");
    const std::array<RowReference, 1> ref{reference};
    const std::array<float, 1> close{1.0F + 0x1p-10F};
    check(compare_rows(close, ref, 1, false).pass, "general path accepts deviation inside declared budget");
    check(!compare_rows(close, ref, 1, true).pass, "exact diagnostics cannot silently use approximate budget");
    check(!compare_rows(std::array<float, 1>{1.01F}, ref, 1, false).pass, "general path rejects deviation outside budget");
    const std::array<RowReference, 1> zero{{{0, 0}}};
    check(compare_rows(std::array<float, 1>{-0.0F}, zero, 1, true).pass, "exact zero accepts sign difference");
    rejects<std::invalid_argument>([&] { compare_rows({}, ref, 1, false); }, "comparison length mismatch rejected");
    rejects<std::invalid_argument>([] { compare_rows({}, {}, 1, false); }, "empty comparison rejected");
    rejects<std::invalid_argument>([&] { compare_rows(close, ref, 0, false); }, "zero comparison width rejected");
    rejects<std::invalid_argument>([&] { compare_rows(close, ref, 129, false); }, "oversized comparison width rejected");
}

void deliberately_wrong_results() {
    const auto input = prepare_input({33, 33}, make_input({33, 33}, "small_integers"));
    const auto correct = rounded_reference(input);
    auto shifted = correct;
    std::rotate(shifted.begin(), shifted.begin() + 1, shifted.end());
    check(!compare_rows(shifted, input.reference, 33, true).pass, "shifted rows rejected");
    auto omitted = correct;
    omitted[5] -= 1;
    const auto omission = compare_rows(omitted, input.reference, 33, true);
    check(!omission.pass && !omission.rows[5].pass && omission.rows[4].pass, "missing term identified at exact failed row");
    check(omission.max_abs_error == 1.0 && omission.rows[5].tolerance == 0.0, "diagnostic error and zero tolerance reported");
    const auto full = prepare_input({1, 33}, make_input({1, 33}, "ones"));
    check(!compare_rows(std::array<float, 1>{32}, full.reference, 33, true).pass, "missing last width tile rejected");
    check(!compare_rows(std::array<float, 1>{65}, full.reference, 33, true).pass, "duplicated width tile rejected");
    const auto random = prepare_input({65, 97}, make_input({65, 97}, "random", 42));
    auto corrupted = rounded_reference(random);
    corrupted[8] += 1.0F;
    check(!compare_rows(corrupted, random.reference, 97, false).pass, "large error in random output rejected");
    const auto zero = prepare_input({33, 33}, make_input({33, 33}, "zero"));
    check(!compare_rows(correct, zero.reference, 33, true).pass, "stale previous result rejected for same shape");
}

void nonfinite_comparator_rejection() {
    const std::array<RowReference, 1> ref{{{0, 0}}};
    for (const auto value : {std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity(),
                             -std::numeric_limits<float>::infinity()}) {
        const auto result = compare_rows(std::array<float, 1>{value}, ref, 32, false);
        check(!result.pass && !result.rows[0].finite, "nonfinite actual rejected");
        const auto json = comparison_json(result);
        check(json.find("\"actual\":null") != std::string::npos, "invalid float emits JSON null");
        check(json.find("\"finite\":false") != std::string::npos, "JSON retains numerical failure flag");
    }
    for (const auto bad_ref : std::array<RowReference, 4>{{
             {std::numeric_limits<double>::quiet_NaN(), 0}, {0, std::numeric_limits<double>::infinity()},
             {0, -1}, {std::numeric_limits<double>::max(), std::numeric_limits<double>::max()},
         }}) {
        check(!compare_rows(std::array<float, 1>{0}, std::array<RowReference, 1>{bad_ref}, 32, false).pass,
              "invalid/nonfinite reference data cannot pass");
    }
}
} // namespace

int main() {
    const std::array<std::pair<std::string_view, std::function<void()>>, 12> tests{{
        {"bf16_known_answers", bf16_known_answers},
        {"shape_and_length_rejection", shape_and_length_rejection},
        {"values_rejection", values_rejection},
        {"face_known_coordinates", face_known_coordinates},
        {"face_independent_enumeration", face_independent_enumeration},
        {"quantized_oracle_known_answer", quantized_oracle_known_answer},
        {"all_shapes_all_patterns", all_shapes_all_patterns},
        {"deterministic_generation", deterministic_generation},
        {"output_extract_and_padding", output_extract_and_padding},
        {"numeric_budget_and_exact_path", numeric_budget_and_exact_path},
        {"deliberately_wrong_results", deliberately_wrong_results},
        {"nonfinite_comparator_rejection", nonfinite_comparator_rejection},
    }};
    std::size_t passed = 0;
    for (const auto& [name, test] : tests) {
        try {
            test();
            std::cout << "PASS " << name << '\n';
            ++passed;
        } catch (const std::exception& error) {
            std::cerr << "FAIL " << name << ": " << error.what() << '\n';
        }
    }
    std::cout << "C0 CPU contract: " << passed << '/' << tests.size() << " groups passed; "
              << assertions << " assertions; 84 shape/pattern cases (12 shapes x 7 patterns).\n"
              << "No Metal, JIT, simulator, or hardware execution in this executable.\n";
    return passed == tests.size() ? 0 : 1;
}
