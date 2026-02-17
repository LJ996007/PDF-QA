export async function sha256File(file: File): Promise<string> {
    const buf = await file.arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', buf);
    const bytes = new Uint8Array(digest);
    // hex
    let out = '';
    for (const b of bytes) {
        out += b.toString(16).padStart(2, '0');
    }
    return out;
}

