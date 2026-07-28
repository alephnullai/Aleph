use std::ptr;

fn raw_pointer_example(data: &[i32]) -> i32 {
    unsafe {
        let ptr = data.as_ptr();
        *ptr.add(0) + *ptr.add(1)
    }
}

fn transmute_bytes(bytes: [u8; 4]) -> f32 {
    unsafe {
        std::mem::transmute(bytes)
    }
}

struct RawBuffer {
    ptr: *mut u8,
    len: usize,
}

impl RawBuffer {
    fn new(size: usize) -> Self {
        let ptr = unsafe {
            let layout = std::alloc::Layout::from_size_align(size, 1).unwrap();
            std::alloc::alloc(layout)
        };
        RawBuffer { ptr, len: size }
    }

    fn write(&mut self, offset: usize, value: u8) {
        assert!(offset < self.len);
        unsafe {
            ptr::write(self.ptr.add(offset), value);
        }
    }

    fn read(&self, offset: usize) -> u8 {
        assert!(offset < self.len);
        unsafe {
            ptr::read(self.ptr.add(offset))
        }
    }
}

impl Drop for RawBuffer {
    fn drop(&mut self) {
        unsafe {
            let layout = std::alloc::Layout::from_size_align(self.len, 1).unwrap();
            std::alloc::dealloc(self.ptr, layout);
        }
    }
}

fn main() {
    let data = [10, 20, 30, 40];
    let sum = raw_pointer_example(&data);
    println!("Sum: {}", sum);

    let bytes: [u8; 4] = [0, 0, 128, 63];
    let float_val = transmute_bytes(bytes);
    println!("Float: {}", float_val);

    let mut buf = RawBuffer::new(16);
    buf.write(0, 42);
    println!("Read: {}", buf.read(0));
}
