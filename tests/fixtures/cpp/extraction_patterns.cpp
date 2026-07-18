// extraction_patterns.cpp — a license-clean, ORIGINAL C++ fixture (authored for
// Aleph's test suite, not copied from any project) that concentrates the
// declaration shapes which broke C++ symbol-name extraction on real corpora
// (OpenTTD, 2026-07-15). Each shape below once caused the extractor to capture
// a TYPE or a raw keyword/statement as the symbol name instead of the
// identifier. The paired test (tests/unit/test_extractor.py) asserts every
// name resolves to the identifier — and that no type/keyword ever leaks in.
//
// Patterns covered:
//   - non-primitive return (std::string)         - destructor
//   - pointer / reference return                  - operator overload
//   - class-type data member                      - nested namespace
//   - out-of-line qualified definition            - using-declaration (import)
//   - template method returning T                 - trailing-return auto -> T

#include <string>
#include <vector>
#include <cstdint>

namespace transport {
namespace rail {

using Money = std::int64_t;

struct Position {
    int x;
    int y;

    // operator overload: the name is `operator==`, not `bool`.
    bool operator==(const Position& other) const {
        return x == other.x && y == other.y;
    }
};

class Station {
public:
    Station(std::string name, Position where)
        : name_(name), where_(where) {}
    virtual ~Station() = default;                 // destructor: name is ~Station

    // non-primitive return: name is `label`, not `std::string`.
    std::string label() const { return name_; }

    // reference return: name is `location`, not `Position`.
    const Position& location() const { return where_; }

    // trailing-return auto: name is `origin`, not `Position`/`auto`.
    auto origin() const -> Position { return where_; }

    virtual Money maintenanceCost() const = 0;

private:
    std::string name_;        // primitive-ish (std::string) member: name is name_
    Position where_;          // class-type member: name is where_, not Position
};

class Depot : public Station {
public:
    // inheriting constructor — using-declaration: imports Station::Station.
    using Station::Station;

    // pointer return: name is `parent`, not `Station`.
    Station* parent() const { return parent_; }

    Money maintenanceCost() const override;       // declared here, defined below

    // template method returning T: name is `roundTrip`, not `T`.
    template <typename T>
    T roundTrip(T value) const { return value; }

private:
    Station* parent_ = nullptr;
};

// out-of-line qualified definition: name resolves to Depot::maintenanceCost,
// NOT the return type `Money`.
Money Depot::maintenanceCost() const {
    return Money{100};
}

}  // namespace rail
}  // namespace transport
