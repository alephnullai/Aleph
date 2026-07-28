#include <vector>
#include <string>
#include <unordered_map>
#include <memory>
#include <algorithm>
#include <functional>
#include <stdexcept>
#include <iostream>
#include <sstream>
#include <numeric>

namespace data_processing {

class DataValidator {
public:
    bool validateInput(const std::string& input, int minLength, int maxLength) {
        if (input.empty()) {
            return false;
        }
        if (static_cast<int>(input.length()) < minLength) {
            return false;
        }
        if (static_cast<int>(input.length()) > maxLength) {
            return false;
        }
        for (char c : input) {
            if (!std::isalnum(c) && c != '_' && c != '-') {
                return false;
            }
        }
        return true;
    }

    std::vector<std::string> sanitizeInputs(const std::vector<std::string>& inputs) {
        std::vector<std::string> result;
        result.reserve(inputs.size());
        for (const auto& input : inputs) {
            std::string sanitized;
            sanitized.reserve(input.size());
            for (char c : input) {
                if (std::isalnum(c) || c == '_' || c == '-') {
                    sanitized.push_back(c);
                }
            }
            if (!sanitized.empty()) {
                result.push_back(std::move(sanitized));
            }
        }
        return result;
    }

    bool validateRange(double value, double min, double max) {
        if (std::isnan(value) || std::isinf(value)) {
            return false;
        }
        return value >= min && value <= max;
    }
};

class DataTransformer {
public:
    std::vector<double> normalizeValues(const std::vector<double>& values) {
        if (values.empty()) {
            return {};
        }
        double minVal = *std::min_element(values.begin(), values.end());
        double maxVal = *std::max_element(values.begin(), values.end());
        double range = maxVal - minVal;
        if (range == 0.0) {
            return std::vector<double>(values.size(), 0.5);
        }
        std::vector<double> normalized;
        normalized.reserve(values.size());
        for (double v : values) {
            normalized.push_back((v - minVal) / range);
        }
        return normalized;
    }

    std::unordered_map<std::string, double> computeStatistics(const std::vector<double>& values) {
        std::unordered_map<std::string, double> stats;
        if (values.empty()) {
            stats["count"] = 0;
            stats["mean"] = 0;
            stats["min"] = 0;
            stats["max"] = 0;
            stats["stddev"] = 0;
            return stats;
        }
        double sum = std::accumulate(values.begin(), values.end(), 0.0);
        double mean = sum / values.size();
        double minVal = *std::min_element(values.begin(), values.end());
        double maxVal = *std::max_element(values.begin(), values.end());
        double sqSum = 0.0;
        for (double v : values) {
            sqSum += (v - mean) * (v - mean);
        }
        double stddev = std::sqrt(sqSum / values.size());
        stats["count"] = static_cast<double>(values.size());
        stats["mean"] = mean;
        stats["min"] = minVal;
        stats["max"] = maxVal;
        stats["stddev"] = stddev;
        stats["sum"] = sum;
        return stats;
    }

    std::vector<double> applyMovingAverage(const std::vector<double>& values, int windowSize) {
        if (values.empty() || windowSize <= 0) {
            return {};
        }
        std::vector<double> result;
        result.reserve(values.size());
        for (size_t i = 0; i < values.size(); ++i) {
            double sum = 0.0;
            int count = 0;
            for (int j = std::max(0, static_cast<int>(i) - windowSize + 1);
                 j <= static_cast<int>(i); ++j) {
                sum += values[j];
                count++;
            }
            result.push_back(sum / count);
        }
        return result;
    }

    std::vector<std::pair<std::string, double>> rankByFrequency(
        const std::vector<std::string>& items) {
        std::unordered_map<std::string, int> frequency;
        for (const auto& item : items) {
            frequency[item]++;
        }
        std::vector<std::pair<std::string, double>> ranked;
        ranked.reserve(frequency.size());
        double total = static_cast<double>(items.size());
        for (const auto& [key, count] : frequency) {
            ranked.emplace_back(key, count / total);
        }
        std::sort(ranked.begin(), ranked.end(),
                  [](const auto& a, const auto& b) { return a.second > b.second; });
        return ranked;
    }
};

class DataPipeline {
private:
    DataValidator validator_;
    DataTransformer transformer_;
    std::vector<std::function<std::vector<double>(const std::vector<double>&)>> stages_;

public:
    void addStage(std::function<std::vector<double>(const std::vector<double>&)> stage) {
        stages_.push_back(std::move(stage));
    }

    std::vector<double> execute(const std::vector<double>& input) {
        std::vector<double> current = input;
        for (const auto& stage : stages_) {
            current = stage(current);
            if (current.empty()) {
                throw std::runtime_error("Pipeline stage produced empty output");
            }
        }
        return current;
    }

    std::vector<double> executeWithValidation(const std::vector<double>& input,
                                               double minVal, double maxVal) {
        for (double v : input) {
            if (!validator_.validateRange(v, minVal, maxVal)) {
                throw std::invalid_argument("Input value out of range: " + std::to_string(v));
            }
        }
        auto result = execute(input);
        auto stats = transformer_.computeStatistics(result);
        std::cout << "Pipeline complete. Mean=" << stats["mean"]
                  << " StdDev=" << stats["stddev"] << std::endl;
        return result;
    }

    std::string summarize(const std::vector<double>& values) {
        auto stats = transformer_.computeStatistics(values);
        std::ostringstream oss;
        oss << "Count: " << stats["count"]
            << ", Mean: " << stats["mean"]
            << ", Min: " << stats["min"]
            << ", Max: " << stats["max"]
            << ", StdDev: " << stats["stddev"];
        return oss.str();
    }
};

} // namespace data_processing

int main() {
    using namespace data_processing;

    DataPipeline pipeline;
    DataTransformer transformer;

    pipeline.addStage([&transformer](const std::vector<double>& v) {
        return transformer.normalizeValues(v);
    });
    pipeline.addStage([&transformer](const std::vector<double>& v) {
        return transformer.applyMovingAverage(v, 3);
    });

    std::vector<double> data = {1.0, 5.0, 3.0, 8.0, 2.0, 9.0, 4.0, 7.0, 6.0, 10.0};

    try {
        auto result = pipeline.executeWithValidation(data, 0.0, 100.0);
        std::cout << pipeline.summarize(result) << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
