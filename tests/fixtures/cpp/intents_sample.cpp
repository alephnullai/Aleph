#include <cassert>
#include <stdexcept>
#include <string>
#include <vector>
#include <iostream>

class Validator {
public:
    bool validateAge(int age) {
        assert(age >= 0);
        return age >= 18 && age <= 150;
    }

    const std::string& getName() const {
        return name_;
    }

    void setName(const std::string& name) {
        if (name.empty()) {
            throw std::invalid_argument("Name cannot be empty");
        }
        name_ = name;
    }

private:
    std::string name_;
};

class SafeProcessor {
public:
    int process(const std::vector<int>& data) {
        try {
            if (data.empty()) {
                throw std::runtime_error("Empty data");
            }
            int sum = 0;
            for (int v : data) {
                sum += v;
            }
            return sum;
        } catch (const std::exception& e) {
            std::cerr << "Error: " << e.what() << std::endl;
            return -1;
        }
    }
};

constexpr int MAX_SIZE = 1024;

int main() {
    Validator v;
    v.setName("Alice");
    assert(v.validateAge(25));

    SafeProcessor sp;
    std::vector<int> data = {1, 2, 3};
    int result = sp.process(data);
    return result >= 0 ? 0 : 1;
}
