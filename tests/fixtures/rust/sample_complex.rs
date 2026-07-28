use std::fmt;

#[derive(Debug, Clone)]
struct Point {
    x: f64,
    y: f64,
}

impl Point {
    fn new(x: f64, y: f64) -> Self {
        Point { x, y }
    }

    fn distance_to(&self, other: &Point) -> f64 {
        let dx = other.x - self.x;
        let dy = other.y - self.y;
        (dx * dx + dy * dy).sqrt()
    }
}

impl fmt::Display for Point {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({}, {})", self.x, self.y)
    }
}

trait Shape: fmt::Display {
    fn area(&self) -> f64;
    fn perimeter(&self) -> f64;
    fn name(&self) -> &str;
}

struct Circle {
    center: Point,
    radius: f64,
}

impl Circle {
    fn new(center: Point, radius: f64) -> Result<Self, String> {
        if radius <= 0.0 {
            return Err("Radius must be positive".to_string());
        }
        Ok(Circle { center, radius })
    }
}

impl Shape for Circle {
    fn area(&self) -> f64 {
        std::f64::consts::PI * self.radius * self.radius
    }

    fn perimeter(&self) -> f64 {
        2.0 * std::f64::consts::PI * self.radius
    }

    fn name(&self) -> &str {
        "Circle"
    }
}

impl fmt::Display for Circle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Circle(center={}, radius={})", self.center, self.radius)
    }
}

struct Rectangle {
    top_left: Point,
    width: f64,
    height: f64,
}

impl Rectangle {
    fn new(top_left: Point, width: f64, height: f64) -> Result<Self, String> {
        if width <= 0.0 || height <= 0.0 {
            return Err("Dimensions must be positive".to_string());
        }
        Ok(Rectangle { top_left, width, height })
    }
}

impl Shape for Rectangle {
    fn area(&self) -> f64 {
        self.width * self.height
    }

    fn perimeter(&self) -> f64 {
        2.0 * (self.width + self.height)
    }

    fn name(&self) -> &str {
        "Rectangle"
    }
}

impl fmt::Display for Rectangle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Rectangle(top_left={}, {}x{})", self.top_left, self.width, self.height)
    }
}

struct ShapeCollection {
    shapes: Vec<Box<dyn Shape>>,
}

impl ShapeCollection {
    fn new() -> Self {
        ShapeCollection { shapes: Vec::new() }
    }

    fn add(&mut self, shape: Box<dyn Shape>) {
        self.shapes.push(shape);
    }

    fn total_area(&self) -> f64 {
        self.shapes.iter().map(|s| s.area()).sum()
    }

    fn total_perimeter(&self) -> f64 {
        self.shapes.iter().map(|s| s.perimeter()).sum()
    }

    fn count(&self) -> usize {
        self.shapes.len()
    }
}

fn main() {
    let mut collection = ShapeCollection::new();

    let circle = Circle::new(Point::new(0.0, 0.0), 5.0).unwrap();
    collection.add(Box::new(circle));

    let rect = Rectangle::new(Point::new(1.0, 1.0), 4.0, 6.0).unwrap();
    collection.add(Box::new(rect));

    println!("Total shapes: {}", collection.count());
    println!("Total area: {:.2}", collection.total_area());
    println!("Total perimeter: {:.2}", collection.total_perimeter());
}
