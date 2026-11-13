class ShortUUIDConverter:
    regex = r"[A-Za-z0-9]{12}"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return str(value)