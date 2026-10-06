# Deliberately minimal: this fixture must stay clean under Odoo's ruff config,
# so every rule it does not exercise is one that cannot flake the self-test.


def add(left, right):
    """Return the sum of two numbers."""
    return left + right
