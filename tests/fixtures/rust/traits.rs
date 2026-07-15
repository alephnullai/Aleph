use std::fmt;

trait Drawable {
    fn draw(&self);
    fn bounding_box(&self) -> (f64, f64, f64, f64);
}

trait Scalable {
    fn scale(&mut self, factor: f64);
}

struct Line {
    x1: f64,
    y1: f64,
    x2: f64,
    y2: f64,
}

impl Line {
    fn new(x1: f64, y1: f64, x2: f64, y2: f64) -> Self {
        Line { x1, y1, x2, y2 }
    }

    fn length(&self) -> f64 {
        let dx = self.x2 - self.x1;
        let dy = self.y2 - self.y1;
        (dx * dx + dy * dy).sqrt()
    }
}

impl Drawable for Line {
    fn draw(&self) {
        println!("Line from ({},{}) to ({},{})", self.x1, self.y1, self.x2, self.y2);
    }

    fn bounding_box(&self) -> (f64, f64, f64, f64) {
        (
            self.x1.min(self.x2),
            self.y1.min(self.y2),
            self.x1.max(self.x2),
            self.y1.max(self.y2),
        )
    }
}

impl Scalable for Line {
    fn scale(&mut self, factor: f64) {
        self.x1 *= factor;
        self.y1 *= factor;
        self.x2 *= factor;
        self.y2 *= factor;
    }
}

impl fmt::Display for Line {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Line[({},{})->({},{})]", self.x1, self.y1, self.x2, self.y2)
    }
}

struct Canvas {
    shapes: Vec<Box<dyn Drawable>>,
}

impl Canvas {
    fn new() -> Self {
        Canvas { shapes: Vec::new() }
    }

    fn add(&mut self, shape: Box<dyn Drawable>) {
        self.shapes.push(shape);
    }

    fn draw_all(&self) {
        for shape in &self.shapes {
            shape.draw();
        }
    }

    fn total_bounding_box(&self) -> Option<(f64, f64, f64, f64)> {
        if self.shapes.is_empty() {
            return None;
        }
        let mut min_x = f64::MAX;
        let mut min_y = f64::MAX;
        let mut max_x = f64::MIN;
        let mut max_y = f64::MIN;
        for shape in &self.shapes {
            let (x1, y1, x2, y2) = shape.bounding_box();
            min_x = min_x.min(x1);
            min_y = min_y.min(y1);
            max_x = max_x.max(x2);
            max_y = max_y.max(y2);
        }
        Some((min_x, min_y, max_x, max_y))
    }
}

fn main() {
    let mut canvas = Canvas::new();
    canvas.add(Box::new(Line::new(0.0, 0.0, 10.0, 10.0)));
    canvas.add(Box::new(Line::new(5.0, 5.0, 15.0, 0.0)));
    canvas.draw_all();

    if let Some(bb) = canvas.total_bounding_box() {
        println!("Bounding box: ({},{}) to ({},{})", bb.0, bb.1, bb.2, bb.3);
    }
}
