"""In-memory ImageStorage for tests: no network, no credentials."""
from app.services.storage import StorageError


class InMemoryStorage:
    configured = True

    def __init__(self, key_prefix="staging", base="https://cdn.example.test", fail_put_after=None):
        self.key_prefix = key_prefix
        self.base = base
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.fail_put_after = fail_put_after  # fail the Nth put (0-based) to test cleanup

    def public_url(self, key):
        return f"{self.base}/{key}"

    def put(self, key, data, content_type):
        if self.fail_put_after is not None and len(self.objects) >= self.fail_put_after:
            raise StorageError("Could not store the image.")
        self.objects[key] = (data, content_type)
        return self.public_url(key)

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def owns(self, key, url):
        return key.startswith(f"{self.key_prefix}/") and url == self.public_url(key)
