use std::f64::consts::PI;

fn calculate_distance(x1: f64, y1: f64, x2: f64, y2: f64) -> f64 {
    let dx = x2 - x1;
    let dy = y2 - y1;
    (dx * dx + dy * dy).sqrt()
}

fn calculate_area(radius: f64) -> f64 {
    PI * radius * radius
}

fn print_result(label: &str, value: f64) {
    println!("{}: {}", label, value);
}

fn main() {
    let dist = calculate_distance(0.0, 0.0, 3.0, 4.0);
    print_result("Distance", dist);

    let area = calculate_area(5.0);
    print_result("Area", area);
}
