"""Check recorded API exchanges against JSON OpenAPI contracts."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def resolve(spec, node, stack=()):
    if not isinstance(node, dict) or "$ref" not in node:
        return node
    ref = node["$ref"]
    if not ref.startswith("#/") or ref in stack:
        raise ValueError(f"Unsupported or cyclic reference: {ref}")
    target = spec
    for piece in ref[2:].split("/"):
        target = target[piece.replace("~1", "/").replace("~0", "~")]
    return resolve(spec, target, stack + (ref,))


def check_schema(spec, schema, value, location="$", depth=0):
    if depth > 32:
        raise ValueError("Schema nesting exceeds 32")
    schema = resolve(spec, schema)
    errors = []
    if value is None and schema.get("nullable"):
        return errors
    kind = schema.get("type")
    types = {"object": lambda v: isinstance(v, dict),
             "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str),
             "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
             "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
             "boolean": lambda v: isinstance(v, bool)}
    if kind in types and not types[kind](value):
        return [f"{location}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: value is outside enum")
    if kind == "object":
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{location}.{field}: required")
        props = schema.get("properties", {})
        for field, item in value.items():
            if field in props:
                errors.extend(check_schema(spec, props[field], item, f"{location}.{field}", depth + 1))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{location}.{field}: unexpected property")
    if kind == "array":
        for index, item in enumerate(value):
            errors.extend(check_schema(spec, schema.get("items", {}), item, f"{location}[{index}]", depth + 1))
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{location}: too few items")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{location}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{location}: too long")
        if schema.get("format") == "date-time":
            from datetime import datetime
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{location}: invalid date-time")
    if kind in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{location}: above maximum")
    return errors


def route(spec, method, path):
    matches = []
    for template, operations in spec.get("paths", {}).items():
        pattern = "^" + re.sub(r"\{([^/{}]+)\}", r"(?P<\1>[^/]+)", re.escape(template).replace("\\{", "{").replace("\\}", "}")) + "$"
        match = re.fullmatch(pattern, path)
        if match and method.lower() in operations:
            matches.append((template.count("{"), template, operations[method.lower()], match.groupdict(), operations))
    return min(matches, default=None, key=lambda item: item[0])


def media_schema(spec, content, media_type, location):
    if not content:
        return None, []
    media_type = media_type.split(";")[0].strip().lower()
    if media_type not in content:
        return None, [f"{location}: unsupported content type {media_type}"]
    return content[media_type].get("schema", {}), []


def validate_case(spec, case):
    parsed = urlsplit(case["path"])
    found = route(spec, case["method"], parsed.path)
    if found is None:
        return [f"request.path: no operation for {case['method'].upper()} {parsed.path}"]
    _, _, operation, path_values, path_item = found
    errors = []
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    query.update(case.get("query", {}))
    parameters = path_item.get("parameters", []) + operation.get("parameters", [])
    for raw in parameters:
        param = resolve(spec, raw)
        source = path_values if param["in"] == "path" else query if param["in"] == "query" else None
        if source is None:
            continue
        name = param["name"]
        location = f"request.{param['in']}.{name}"
        if name not in source:
            if param.get("required"):
                errors.append(f"{location}: required")
            continue
        value = source[name]
        schema = resolve(spec, param.get("schema", {}))
        if isinstance(value, str):
            try:
                if schema.get("type") == "integer":
                    value = int(value)
                elif schema.get("type") == "number":
                    value = float(value)
                elif schema.get("type") == "boolean":
                    if value.lower() not in ("true", "false"):
                        raise ValueError()
                    value = value.lower() == "true"
            except ValueError:
                errors.append(f"{location}: invalid {schema.get('type')}")
                continue
        errors.extend(check_schema(spec, schema, value, location))
    body_spec = resolve(spec, operation.get("requestBody", {}))
    if body_spec.get("required") and "body" not in case:
        errors.append("request.body: required")
    elif "body" in case and body_spec.get("content"):
        schema, media_errors = media_schema(spec, body_spec["content"], case.get("request_content_type", "application/json"), "request")
        errors.extend(media_errors)
        if schema is not None:
            errors.extend(check_schema(spec, schema, case["body"], "request.body"))
    responses = operation.get("responses", {})
    response_spec = responses.get(str(case["status"]), responses.get("default"))
    if response_spec is None:
        errors.append(f"response.status: undocumented {case['status']}")
    else:
        content = resolve(spec, response_spec).get("content", {})
        if content:
            schema, media_errors = media_schema(spec, content, case.get("response_content_type", "application/json"), "response")
            errors.extend(media_errors)
            if "response" not in case:
                errors.append("response.body: missing")
            elif schema is not None:
                errors.extend(check_schema(spec, schema, case["response"], "response.body"))
    return errors


def run(spec, cases):
    return [{"case": index + 1, "passed": not (errors := validate_case(spec, case)), "errors": errors}
            for index, case in enumerate(cases)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec")
    parser.add_argument("cases")
    parser.add_argument("--json", help="Write full report to path")
    args = parser.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = run(spec, cases)
    for item in report:
        print(f"Case {item['case']}: {'PASS' if item['passed'] else 'FAIL'}")
        for error in item["errors"]:
            print("  " + error)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(1 if any(not item["passed"] for item in report) else 0)


if __name__ == "__main__":
    main()

