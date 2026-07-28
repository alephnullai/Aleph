use std::fs;
use std::io;
use std::num::ParseIntError;

#[derive(Debug)]
enum AppError {
    Io(io::Error),
    Parse(ParseIntError),
    Custom(String),
}

fn read_config(path: &str) -> Result<String, AppError> {
    fs::read_to_string(path).map_err(AppError::Io)
}

fn parse_port(s: &str) -> Result<u16, AppError> {
    s.trim().parse::<u16>().map_err(AppError::Parse)
}

fn load_config(path: &str) -> Result<u16, AppError> {
    let content = read_config(path)?;
    let port = parse_port(&content)?;
    if port < 1024 {
        return Err(AppError::Custom("Port must be >= 1024".to_string()));
    }
    Ok(port)
}

fn validate_input(data: &[u8]) -> Result<(), AppError> {
    if data.is_empty() {
        return Err(AppError::Custom("Empty input".to_string()));
    }
    Ok(())
}

fn process_data(data: &[u8]) -> Result<Vec<u8>, AppError> {
    validate_input(data)?;
    Ok(data.iter().map(|b| b.wrapping_add(1)).collect())
}

fn safe_divide(a: f64, b: f64) -> Result<f64, AppError> {
    if b == 0.0 {
        return Err(AppError::Custom("Division by zero".to_string()));
    }
    Ok(a / b)
}

fn main() {
    match load_config("config.txt") {
        Ok(port) => println!("Port: {}", port),
        Err(e) => eprintln!("Error: {:?}", e),
    }

    let data = vec![1, 2, 3];
    match process_data(&data) {
        Ok(result) => println!("Processed: {:?}", result),
        Err(e) => eprintln!("Error: {:?}", e),
    }
}
