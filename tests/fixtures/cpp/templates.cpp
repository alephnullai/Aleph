#include <vector>
#include <string>
#include <algorithm>
#include <functional>
#include <numeric>

template<typename T>
class Stack {
public:
    void push(const T& value) {
        data_.push_back(value);
    }

    T pop() {
        if (data_.empty()) {
            throw std::runtime_error("Stack underflow");
        }
        T value = data_.back();
        data_.pop_back();
        return value;
    }

    bool empty() const {
        return data_.empty();
    }

    size_t size() const {
        return data_.size();
    }

private:
    std::vector<T> data_;
};

template<typename T>
T findMax(const std::vector<T>& values) {
    if (values.empty()) {
        throw std::invalid_argument("Cannot find max of empty vector");
    }
    return *std::max_element(values.begin(), values.end());
}

template<typename T, typename Pred>
std::vector<T> filterBy(const std::vector<T>& values, Pred predicate) {
    std::vector<T> result;
    std::copy_if(values.begin(), values.end(), std::back_inserter(result), predicate);
    return result;
}

template<typename T>
T accumulate(const std::vector<T>& values, T init) {
    return std::accumulate(values.begin(), values.end(), init);
}

int main() {
    Stack<int> intStack;
    intStack.push(1);
    intStack.push(2);
    intStack.push(3);

    int maxVal = findMax(std::vector<int>{1, 5, 3, 7, 2});

    auto evens = filterBy(std::vector<int>{1, 2, 3, 4, 5}, [](int x) { return x % 2 == 0; });

    int sum = accumulate(std::vector<int>{1, 2, 3, 4, 5}, 0);

    return 0;
}
