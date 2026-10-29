"""How a failure in plugin code is told to the user: by the exceptions that caused it."""

from kinby.plugins.errors import exception_message


def test_a_nested_exception_group_is_told_by_its_leaf() -> None:
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [ValueError("no such package")])])

    assert exception_message(nested) == "ValueError: no such package"


def test_a_group_of_two_leaves_names_both() -> None:
    group = ExceptionGroup("outer", [ValueError("first"), KeyError("second")])

    assert exception_message(group) == "ValueError: first; KeyError: 'second'"
