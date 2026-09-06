"""Original synthetic fixture: a small cache with explicit time inputs."""


class TimedCache:
    def __init__(self):
        self.entries = {}

    def put(self, key, value, now, ttl):
        """Store a value until its time-to-live expires."""
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.entries[key] = (value, now + ttl)

    def get(self, key, now):
        """Return a cached value; remove it when it has expired."""
        entry = self.entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if now >= expires_at:
            del self.entries[key]
            return None
        return value
