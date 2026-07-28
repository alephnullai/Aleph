#include <vector>
#include <string>
#include <memory>
#include <algorithm>
#include <stdexcept>

namespace geometry {

struct Point {
    double x;
    double y;

    Point(double x, double y) : x(x), y(y) {}

    double distanceTo(const Point& other) const {
        double dx = other.x - x;
        double dy = other.y - y;
        return std::sqrt(dx * dx + dy * dy);
    }
};

class Shape {
public:
    virtual ~Shape() = default;
    virtual double area() const = 0;
    virtual double perimeter() const = 0;
    virtual std::string name() const = 0;
};

class Circle : public Shape {
private:
    Point center_;
    double radius_;
public:
    Circle(Point center, double radius) : center_(center), radius_(radius) {
        if (radius <= 0) {
            throw std::invalid_argument("Radius must be positive");
        }
    }

    double area() const override {
        return 3.14159265358979 * radius_ * radius_;
    }

    double perimeter() const override {
        return 2.0 * 3.14159265358979 * radius_;
    }

    std::string name() const override { return "Circle"; }

    Point getCenter() const { return center_; }
    double getRadius() const { return radius_; }
};

class Rectangle : public Shape {
private:
    Point topLeft_;
    double width_;
    double height_;
public:
    Rectangle(Point topLeft, double width, double height)
        : topLeft_(topLeft), width_(width), height_(height) {
        if (width <= 0 || height <= 0) {
            throw std::invalid_argument("Dimensions must be positive");
        }
    }

    double area() const override {
        return width_ * height_;
    }

    double perimeter() const override {
        return 2.0 * (width_ + height_);
    }

    std::string name() const override { return "Rectangle"; }

    double getWidth() const { return width_; }
    double getHeight() const { return height_; }
};

class ShapeCollection {
private:
    std::vector<std::unique_ptr<Shape>> shapes_;
public:
    void addShape(std::unique_ptr<Shape> shape) {
        shapes_.push_back(std::move(shape));
    }

    double totalArea() const {
        double total = 0.0;
        for (const auto& shape : shapes_) {
            total += shape->area();
        }
        return total;
    }

    double totalPerimeter() const {
        double total = 0.0;
        for (const auto& shape : shapes_) {
            total += shape->perimeter();
        }
        return total;
    }

    size_t count() const { return shapes_.size(); }

    std::vector<std::string> names() const {
        std::vector<std::string> result;
        result.reserve(shapes_.size());
        for (const auto& shape : shapes_) {
            result.push_back(shape->name());
        }
        return result;
    }
};

} // namespace geometry

int main() {
    using namespace geometry;

    ShapeCollection collection;

    auto circle = std::make_unique<Circle>(Point(0.0, 0.0), 5.0);
    collection.addShape(std::move(circle));

    auto rect = std::make_unique<Rectangle>(Point(1.0, 1.0), 4.0, 6.0);
    collection.addShape(std::move(rect));

    std::cout << "Total shapes: " << collection.count() << std::endl;
    std::cout << "Total area: " << collection.totalArea() << std::endl;
    std::cout << "Total perimeter: " << collection.totalPerimeter() << std::endl;

    return 0;
}
