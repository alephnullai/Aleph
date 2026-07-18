// TypeScript import/export patterns

import { readFile, writeFile } from "fs/promises";
import path from "path";
import type { Config } from "./types";

export { UserService } from "./classes";
export default function main(): void {
    console.log("starting");
}

export const VERSION = "1.0.0";

export async function loadConfig(configPath: string): Promise<Config> {
    const raw = await readFile(configPath, "utf-8");
    return JSON.parse(raw);
}

export function resolvePath(...segments: string[]): string {
    return path.resolve(...segments);
}
