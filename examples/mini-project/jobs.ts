/** Original synthetic fixture: an in-memory queue with no external services. */
export class JobQueue {
  private pending = new Map<string, string>();

  /** Queue a payload under a unique identifier. */
  enqueue(id: string, payload: string) {
    if (this.pending.has(id)) throw new Error('duplicate job identifier');
    this.pending.set(id, payload);
  }

  /** Cancel a pending job. */
  cancel(id: string) {
    return this.pending.delete(id);
  }

  /** Remove and return the next queued payload. */
  take() {
    const first = this.pending.entries().next();
    if (first.done) return undefined;
    const [id, payload] = first.value;
    this.pending.delete(id);
    return payload;
  }
}
