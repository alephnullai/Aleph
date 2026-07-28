#include <iostream>
#include <cmath>

const double PI = 3.14159265358979;

double calculateDistance(double x1, double y1, double x2, double y2) {
    double dx = x2 - x1;
    double dy = y2 - y1;
    return std::sqrt(dx * dx + dy * dy);
}

double calculateArea(double radius) {
    return PI * radius * radius;
}

void printResult(const char* label, double value) {
    std::cout << label << ": " << value << std::endl;
}

int main() {
    double dist = calculateDistance(0.0, 0.0, 3.0, 4.0);
    printResult("Distance", dist);

    double area = calculateArea(5.0);
    printResult("Area", area);

    return 0;
}
