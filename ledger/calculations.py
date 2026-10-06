import ast
import operator
from decimal import Decimal, DivisionByZero, InvalidOperation


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class FormulaError(ValueError):
    pass


def _evaluate(node, resolve_name):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, resolve_name)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        return resolve_name(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left, resolve_name)
        right = _evaluate(node.right, resolve_name)
        if isinstance(node.op, ast.Div) and right == 0:
            return None
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        value = _evaluate(node.operand, resolve_name)
        return None if value is None else _UNARY_OPERATORS[type(node.op)](value)
    raise FormulaError("Use only field keys, numbers, and +, -, *, / in formulas.")


def validate_formula(expression, field_keys):
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise FormulaError("Enter a valid arithmetic formula.") from exc
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    unknown = names - set(field_keys)
    if unknown:
        raise FormulaError(f"Unknown field key(s): {', '.join(sorted(unknown))}.")
    _evaluate(tree, lambda name: Decimal("1"))


def calculate_values(fields, input_values):
    field_map = {field.key: field for field in fields}
    results = dict(input_values)
    resolving = set()

    def resolve_name(key):
        if key in results:
            value = results[key]
            if value in (None, ""):
                return Decimal("0")
            try:
                return Decimal(str(value))
            except InvalidOperation as exc:
                raise FormulaError(f"{field_map[key].label} must be numeric for this calculation.") from exc
        field = field_map.get(key)
        if field is None or field.field_type != "calculated":
            raise FormulaError(f"Missing value for {field.label if field else key}.")
        if key in resolving:
            raise FormulaError("Calculated fields cannot refer to themselves or form a cycle.")
        resolving.add(key)
        tree = ast.parse(field.formula, mode="eval")
        value = _evaluate(tree, resolve_name)
        resolving.remove(key)
        results[key] = "" if value is None else str(value.quantize(Decimal("0.01")))
        return value

    try:
        for field in fields:
            if field.field_type == "calculated":
                resolve_name(field.key)
    except (FormulaError, DivisionByZero, InvalidOperation, ZeroDivisionError) as exc:
        raise FormulaError(str(exc)) from exc
    return results