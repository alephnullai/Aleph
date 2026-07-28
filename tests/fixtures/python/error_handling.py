"""Error handling pattern examples for Aleph testing."""


class ValidationError(Exception):
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class NotFoundError(Exception):
    pass


def validate_email(email):
    if not email:
        raise ValidationError("email", "Email is required")
    if "@" not in email:
        raise ValidationError("email", "Invalid email format")
    return True


def validate_age(age):
    if not isinstance(age, int):
        raise TypeError("Age must be an integer")
    if age < 0 or age > 150:
        raise ValueError(f"Age must be between 0 and 150, got {age}")
    return True


def safe_divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None


def parse_config(text):
    try:
        lines = text.strip().split("\n")
        config = {}
        for line in lines:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
        return config
    except ValueError as e:
        raise ValidationError("config", f"Invalid config format: {e}")


def find_user(users, user_id):
    for user in users:
        if user.get("id") == user_id:
            return user
    raise NotFoundError(f"User {user_id} not found")


def process_batch(items):
    results = []
    errors = []
    for item in items:
        try:
            result = process_item(item)
            results.append(result)
        except Exception as e:
            errors.append({"item": item, "error": str(e)})
    return results, errors


def process_item(item):
    assert item is not None, "Item cannot be None"
    if not isinstance(item, dict):
        raise TypeError("Item must be a dict")
    return {"processed": True, **item}
