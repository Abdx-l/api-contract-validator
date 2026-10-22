# API Contract Validator

Validate recorded HTTP request and response pairs against a practical subset of an OpenAPI 3 JSON document. It checks routes, path and query parameters, required request bodies, response status codes, content types, object fields, arrays, enums, formats, and local `$ref` schemas. Findings include a JSON-path-like location for debugging.

```sh
python validate.py examples/openapi.json examples/cases.json
python validate.py examples/openapi.json examples/cases.json --json report.json
python -m unittest discover -s tests -v
```

Cases are JSON objects with `method`, `path`, optional `query`, `body`, and `request_content_type`, plus `status`, `response`, and `response_content_type`. The validator is intentionally a documented subset: JSON payloads, local schema references, common scalar types, and exact path templates. It does not fetch remote references or implement every OpenAPI keyword.

