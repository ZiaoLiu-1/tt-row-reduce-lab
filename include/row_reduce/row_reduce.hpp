#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <locale>
#include <random>
#include <span>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

// Project-owned CPU contract. No Metal or simulator execution occurs here.
namespace row_reduce {

inline constexpr std::size_t tile_side = 32;
inline constexpr std::size_t face_side = 16;
inline constexpr std::size_t max_dimension = 128;

struct Shape {
    std::size_t rows;
    std::size_t cols;
    friend constexpr bool operator==(Shape, Shape) = default;
};

inline std::size_t checked_product(std::size_t a, std::size_t b) {
    if (b != 0 && a > std::numeric_limits<std::size_t>::max() / b) {
        throw std::overflow_error("shape multiplication overflow");
    }
    return a * b;
}

inline void validate_shape(Shape shape, std::size_t nodes = 1) {
    (void)checked_product(shape.rows, shape.cols);
    if (shape.rows == 0 || shape.cols == 0 || shape.rows > max_dimension ||
        shape.cols > max_dimension) {
        throw std::invalid_argument("rows and cols must each be in [1,128]");
    }
    if (nodes != 1) {
        throw std::invalid_argument("this single-node contract requires nodes=1");
    }
}

inline std::size_t padded_dimension(std::size_t dimension) {
    if (dimension == 0 || dimension > max_dimension) {
        throw std::invalid_argument("padding dimension must be in [1,128]");
    }
    return ((dimension + tile_side - 1) / tile_side) * tile_side;
}

// IEEE binary32 -> BF16 round-to-nearest, ties-to-even. NaNs stay NaNs.
inline std::uint16_t float_to_bf16(float value) {
    static_assert(sizeof(float) == sizeof(std::uint32_t));
    static_assert(std::numeric_limits<float>::is_iec559);
    const auto bits = std::bit_cast<std::uint32_t>(value);
    if ((bits & 0x7f800000U) == 0x7f800000U && (bits & 0x007fffffU) != 0) {
        return static_cast<std::uint16_t>((bits >> 16U) | 0x0040U);
    }
    const auto rounded = bits + 0x7fffU + ((bits >> 16U) & 1U);
    return static_cast<std::uint16_t>(rounded >> 16U);
}

inline float bf16_to_float(std::uint16_t bits) {
    return std::bit_cast<float>(static_cast<std::uint32_t>(bits) << 16U);
}

inline void validate_tile_shape(std::size_t rows, std::size_t cols) {
    (void)checked_product(rows, cols);
    if (rows == 0 || cols == 0 || rows % tile_side != 0 || cols % tile_side != 0) {
        throw std::invalid_argument("physical dimensions must be positive multiples of 32");
    }
}

// A tile is face 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right;
// each face is 16x16 row-major, and the tile grid is row-major.
inline std::size_t tile_face_index_unchecked(std::size_t row, std::size_t col,
                                            std::size_t padded_cols) {
    const auto tile = (row / 32) * (padded_cols / 32) + col / 32;
    const auto face = ((row % 32) / 16) * 2 + (col % 32) / 16;
    return tile * 1024 + face * 256 + (row % 16) * 16 + col % 16;
}

inline std::size_t tile_face_index(std::size_t row, std::size_t col,
                                  std::size_t padded_rows, std::size_t padded_cols) {
    validate_tile_shape(padded_rows, padded_cols);
    if (row >= padded_rows || col >= padded_cols) {
        throw std::out_of_range("coordinate outside physical matrix");
    }
    return tile_face_index_unchecked(row, col, padded_cols);
}

template <class T>
std::vector<T> tilize(std::span<const T> row_major, std::size_t rows, std::size_t cols) {
    validate_tile_shape(rows, cols);
    if (row_major.size() != checked_product(rows, cols)) {
        throw std::invalid_argument("tilize data length does not match shape");
    }
    std::vector<T> tiled(row_major.size());
    for (std::size_t row = 0; row < rows; ++row) {
        for (std::size_t col = 0; col < cols; ++col) {
            tiled[tile_face_index_unchecked(row, col, cols)] = row_major[row * cols + col];
        }
    }
    return tiled;
}

template <class T>
std::vector<T> untilize(std::span<const T> tiled, std::size_t rows, std::size_t cols) {
    validate_tile_shape(rows, cols);
    if (tiled.size() != checked_product(rows, cols)) {
        throw std::invalid_argument("untilize data length does not match shape");
    }
    std::vector<T> row_major(tiled.size());
    for (std::size_t row = 0; row < rows; ++row) {
        for (std::size_t col = 0; col < cols; ++col) {
            row_major[row * cols + col] = tiled[tile_face_index_unchecked(row, col, cols)];
        }
    }
    return row_major;
}

struct RowReference {
    double sum = 0;
    double abs_sum = 0;
};

struct PreparedInput {
    Shape shape;
    std::size_t padded_rows;
    std::size_t padded_cols;
    std::vector<std::uint16_t> quantized; // Logical row-major upload values before padding.
    std::vector<std::uint16_t> padded_row_major;
    std::vector<std::uint16_t> tiled;
    std::vector<RowReference> reference;
};

inline PreparedInput prepare_input(Shape shape, std::span<const float> values,
                                   std::size_t nodes = 1) {
    validate_shape(shape, nodes);
    if (values.size() != checked_product(shape.rows, shape.cols)) {
        throw std::invalid_argument("input data length does not match logical shape");
    }
    PreparedInput result{shape, padded_dimension(shape.rows), padded_dimension(shape.cols),
                         {}, {}, {}, {}};
    result.quantized.reserve(values.size());
    result.padded_row_major.assign(result.padded_rows * result.padded_cols, 0x0000U);
    result.reference.resize(shape.rows);
    for (std::size_t row = 0; row < shape.rows; ++row) {
        for (std::size_t col = 0; col < shape.cols; ++col) {
            const auto value = values[row * shape.cols + col];
            if (!std::isfinite(value)) {
                throw std::invalid_argument("input contains NaN or infinity");
            }
            const auto bits = float_to_bf16(value);
            const double q = bf16_to_float(bits);
            const auto magnitude = std::abs(q);
            if (!std::isfinite(q) || (magnitude != 0 &&
                                      (magnitude < 0x1p-8 || magnitude > 1.0))) {
                throw std::invalid_argument("quantized nonzero input magnitude must be in [2^-8,1]");
            }
            result.quantized.push_back(bits);
            result.padded_row_major[row * result.padded_cols + col] = bits;
            result.reference[row].sum += q;
            result.reference[row].abs_sum += magnitude;
        }
    }
    result.tiled = tilize<std::uint16_t>(result.padded_row_major, result.padded_rows,
                                       result.padded_cols);
    return result;
}

inline constexpr std::array<std::string_view, 7> patterns{
    "zero", "one_hot", "ones", "cancellation", "small_integers", "decimals", "random"};

inline bool valid_pattern(std::string_view pattern) {
    return std::find(patterns.begin(), patterns.end(), pattern) != patterns.end();
}

inline bool comparison_is_exact(std::string_view pattern) {
    if (!valid_pattern(pattern)) {
        throw std::invalid_argument("unknown input pattern");
    }
    return pattern != "decimals" && pattern != "random";
}

inline std::vector<float> make_input(Shape shape, std::string_view pattern,
                                    std::uint32_t seed = 1) {
    validate_shape(shape);
    if (!valid_pattern(pattern)) {
        throw std::invalid_argument("unknown input pattern");
    }
    std::vector<float> values(shape.rows * shape.cols, 0.0F);
    std::mt19937 generator(seed);
    constexpr std::array<float, 3> decimals{0.1F, -0.2F, 0.3F};
    for (std::size_t row = 0; row < shape.rows; ++row) {
        for (std::size_t col = 0; col < shape.cols; ++col) {
            auto& value = values[row * shape.cols + col];
            if (pattern == "ones") {
                value = 1.0F;
            } else if (pattern == "one_hot") {
                value = (row + 1 == shape.rows && col + 1 == shape.cols) ? 1.0F : 0.0F;
            } else if (pattern == "cancellation") {
                value = (col + 1 == shape.cols && shape.cols % 2 != 0) ? 0.0F :
                        ((col % 2 == 0) ? 1.0F : -1.0F);
            } else if (pattern == "small_integers") {
                value = col < std::min(row % 17 + 1, shape.cols) ? 1.0F : 0.0F;
            } else if (pattern == "decimals") {
                value = decimals[(row + col) % decimals.size()];
            } else if (pattern == "random") {
                // Specified mt19937 outputs + integer arithmetic avoid distribution differences.
                const auto sample = generator();
                const auto magnitude = 256U + (sample % 65281U);
                value = static_cast<float>(magnitude) / 65536.0F;
                if ((generator() & 1U) != 0) {
                    value = -value;
                }
            }
        }
    }
    return values;
}

struct Case {
    Shape shape;
    std::string_view pattern;
    std::uint32_t seed;
};

inline constexpr std::array<Case, 12> required_cases{{
    {{1, 1}, "zero", 101}, {{1, 31}, "one_hot", 102},
    {{1, 32}, "ones", 103}, {{1, 33}, "decimals", 104},
    {{31, 31}, "cancellation", 105}, {{32, 32}, "small_integers", 106},
    {{32, 33}, "ones", 107}, {{33, 32}, "one_hot", 108},
    {{33, 33}, "small_integers", 109}, {{65, 97}, "random", 110},
    {{97, 65}, "decimals", 111}, {{128, 128}, "random", 112},
}};

// The accepted budget is fixed in the project blueprint before device runs.
inline double error_budget(const RowReference& reference, std::size_t width) {
    if (width == 0 || width > max_dimension) {
        throw std::invalid_argument("comparison width must be in [1,128]");
    }
    constexpr double u32 = 0x1p-24;
    constexpr double ub = 0x1p-8;
    const double k_u32 = static_cast<double>(width + 64) * u32;
    const double gamma = k_u32 / (1.0 - k_u32);
    return ub * std::abs(reference.sum) + (1.0 + ub) * gamma * reference.abs_sum + 0x1p-20;
}

struct RowComparison {
    double actual;
    double reference;
    double abs_error;
    double tolerance;
    double scaled_error;
    bool finite;
    bool pass;
};

struct Comparison {
    bool pass = true;
    double max_abs_error = 0;
    double max_scaled_error = 0;
    std::vector<RowComparison> rows;
};

inline Comparison compare_rows(std::span<const float> actual,
                               std::span<const RowReference> reference,
                               std::size_t width, bool exact) {
    if (actual.size() != reference.size() || actual.empty()) {
        throw std::invalid_argument("actual and reference must have matching nonempty row counts");
    }
    if (actual.size() > max_dimension) {
        throw std::invalid_argument("comparison row count exceeds contract");
    }
    Comparison result;
    result.rows.reserve(actual.size());
    for (std::size_t row = 0; row < actual.size(); ++row) {
        const auto& ref = reference[row];
        const double declared_budget = error_budget(ref, width);
        const double tolerance = exact ? 0.0 : declared_budget;
        const double error = std::abs(static_cast<double>(actual[row]) - ref.sum);
        const double scaled_error = error / std::max(ref.abs_sum, 0x1p-20);
        const bool finite = std::isfinite(actual[row]) && std::isfinite(ref.sum) &&
                            std::isfinite(ref.abs_sum) && ref.abs_sum >= 0 &&
                            std::isfinite(declared_budget) && std::isfinite(error) &&
                            std::isfinite(scaled_error);
        const bool pass = finite && error <= tolerance;
        result.rows.push_back({actual[row], ref.sum, error, tolerance, scaled_error, finite, pass});
        result.pass = result.pass && pass;
        result.max_abs_error = finite ? std::max(result.max_abs_error, error) :
                                       std::numeric_limits<double>::infinity();
        result.max_scaled_error = finite ? std::max(result.max_scaled_error, scaled_error) :
                                          std::numeric_limits<double>::infinity();
    }
    return result;
}

// REDUCE_ROW stores the row sum at column zero of each 32x32 output tile.
// All other columns and padded rows must decode to zero; validate before returning.
inline std::vector<float> decode_output(std::span<const std::uint16_t> tiled, Shape shape) {
    validate_shape(shape);
    const auto row_major = untilize<std::uint16_t>(tiled, padded_dimension(shape.rows), 32);
    std::vector<float> actual(shape.rows);
    for (std::size_t row = 0; row < padded_dimension(shape.rows); ++row) {
        for (std::size_t col = 0; col < 32; ++col) {
            const float value = bf16_to_float(row_major[row * 32 + col]);
            if (row < shape.rows && col == 0) {
                actual[row] = value;
            } else if (!std::isfinite(value) || value != 0.0F) {
                throw std::runtime_error("nonzero or nonfinite output padding at row " +
                                         std::to_string(row) + ", col " + std::to_string(col));
            }
        }
    }
    return actual;
}

// FNV-1a-64 over explicitly little-endian BF16 bytes; an identity checksum,
// not a cryptographic digest. Hash algorithm is always included in its name.
inline std::string quantized_fnv1a64(std::span<const std::uint16_t> bits) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (const auto value : bits) {
        hash ^= value & 0xffU;
        hash *= 1099511628211ULL;
        hash ^= value >> 8U;
        hash *= 1099511628211ULL;
    }
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::hex << std::setfill('0') << std::setw(16) << hash;
    return out.str();
}

inline std::string quantized_bits_hex(std::span<const std::uint16_t> bits) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::hex << std::setfill('0');
    for (const auto value : bits) {
        // Hex-encoded little-endian bytes, e.g. BF16 1 (0x3f80) is "803f".
        out << std::setw(2) << (value & 0xffU) << std::setw(2) << (value >> 8U);
    }
    return out.str();
}

inline void write_json_number(std::ostream& out, double value) {
    if (std::isfinite(value)) {
        out << std::setprecision(17) << value;
    } else {
        out << "null";
    }
}

inline std::string comparison_json(const Comparison& comparison) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << "{\"pass\":" << (comparison.pass ? "true" : "false") << ",\"max_abs_error\":";
    write_json_number(out, comparison.max_abs_error);
    out << ",\"max_scaled_error\":";
    write_json_number(out, comparison.max_scaled_error);
    out << ",\"rows\":[";
    for (std::size_t row = 0; row < comparison.rows.size(); ++row) {
        if (row != 0) out << ',';
        const auto& value = comparison.rows[row];
        out << "{\"row\":" << row << ",\"actual\":";
        write_json_number(out, value.actual);
        out << ",\"reference\":";
        write_json_number(out, value.reference);
        out << ",\"abs_error\":";
        write_json_number(out, value.abs_error);
        out << ",\"tolerance\":";
        write_json_number(out, value.tolerance);
        out << ",\"scaled_error\":";
        write_json_number(out, value.scaled_error);
        out << ",\"finite\":" << (value.finite ? "true" : "false")
            << ",\"pass\":" << (value.pass ? "true" : "false") << '}';
    }
    out << "]}";
    return out.str();
}

} // namespace row_reduce
