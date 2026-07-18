#include <stdexcept>
#include <string>
#include <iostream>
#include <fstream>
#include <vector>

class FileReader {
public:
    std::string readFile(const std::string& path) {
        std::ifstream file(path);
        if (!file.is_open()) {
            throw std::runtime_error("Cannot open file: " + path);
        }
        std::string content;
        std::string line;
        while (std::getline(file, line)) {
            content += line + "\n";
        }
        return content;
    }

    std::vector<std::string> readLines(const std::string& path) {
        std::vector<std::string> lines;
        std::ifstream file(path);
        if (!file.is_open()) {
            throw std::runtime_error("Cannot open file: " + path);
        }
        std::string line;
        while (std::getline(file, line)) {
            lines.push_back(line);
        }
        return lines;
    }
};

class DataParser {
public:
    int parseInt(const std::string& s) {
        try {
            return std::stoi(s);
        } catch (const std::invalid_argument& e) {
            throw std::invalid_argument("Not a number: " + s);
        } catch (const std::out_of_range& e) {
            throw std::out_of_range("Number too large: " + s);
        }
    }

    double parseDouble(const std::string& s) {
        try {
            return std::stod(s);
        } catch (const std::exception& e) {
            return 0.0;
        }
    }
};

int main() {
    FileReader reader;
    DataParser parser;

    try {
        std::string content = reader.readFile("data.txt");
        auto lines = reader.readLines("data.txt");
        for (const auto& line : lines) {
            int val = parser.parseInt(line);
            std::cout << val << std::endl;
        }
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
