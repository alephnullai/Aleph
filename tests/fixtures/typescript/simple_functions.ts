// Simple TypeScript functions for Aleph extraction tests

export function greet(name: string): string {
    return `Hello, ${name}!`;
}

async function fetchData(url: string): Promise<Response> {
    const response = await fetch(url);
    return response;
}

const multiply = (a: number, b: number): number => {
    return a * b;
};

function* generateIds(): Generator<number> {
    let id = 0;
    while (true) {
        yield id++;
    }
}

function processItems(items: string[], callback: (item: string) => void): void {
    items.forEach(callback);
}
