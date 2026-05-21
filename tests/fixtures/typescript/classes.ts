// TypeScript classes, interfaces, enums, and type aliases

interface Serializable {
    serialize(): string;
    deserialize(data: string): void;
}

interface Repository<T> {
    findById(id: string): Promise<T | null>;
    save(entity: T): Promise<void>;
    delete(id: string): Promise<boolean>;
}

class UserService implements Serializable {
    private users: Map<string, User>;

    constructor() {
        this.users = new Map();
    }

    serialize(): string {
        return JSON.stringify(Array.from(this.users.entries()));
    }

    deserialize(data: string): void {
        const entries = JSON.parse(data);
        this.users = new Map(entries);
    }

    async getUser(id: string): Promise<User | null> {
        return this.users.get(id) || null;
    }

    createUser(name: string, email: string): User {
        const user = { id: crypto.randomUUID(), name, email };
        this.users.set(user.id, user);
        return user;
    }
}

enum Status {
    Active = "active",
    Inactive = "inactive",
    Suspended = "suspended",
}

type User = {
    id: string;
    name: string;
    email: string;
};

type Result<T, E = Error> = { ok: true; value: T } | { ok: false; error: E };

export class CacheManager<T> {
    private cache: Map<string, { value: T; expires: number }>;

    constructor(private ttl: number = 300) {
        this.cache = new Map();
    }

    get(key: string): T | undefined {
        const entry = this.cache.get(key);
        if (!entry || Date.now() > entry.expires) {
            this.cache.delete(key);
            return undefined;
        }
        return entry.value;
    }

    set(key: string, value: T): void {
        this.cache.set(key, { value, expires: Date.now() + this.ttl * 1000 });
    }
}
