// JavaScript functions and classes

function calculateTotal(items) {
    return items.reduce((sum, item) => sum + item.price, 0);
}

const formatCurrency = (amount) => {
    return `$${amount.toFixed(2)}`;
};

class EventEmitter {
    constructor() {
        this.listeners = {};
    }

    on(event, callback) {
        if (!this.listeners[event]) {
            this.listeners[event] = [];
        }
        this.listeners[event].push(callback);
    }

    emit(event, ...args) {
        const callbacks = this.listeners[event] || [];
        callbacks.forEach(cb => cb(...args));
    }
}

async function fetchJSON(url) {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
}

export { calculateTotal, formatCurrency, EventEmitter, fetchJSON };
