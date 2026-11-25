"""How /ticker's and /stock's tests read a reply: the plain line, then the spec
the terminal draws, each component checked as the terminal's catalog reads it."""

import json

# What each catalog component requires, as its reader in the terminal does.
REQUIRED = {
    "metric_card": {"label": str, "value": str},
    "labeled_output": {"label": str, "value": str},
    "data_table": {"columns": list, "rows": list},
    "performance_chart": {"label": str},
    "markdown": {"content": str},
    "alert_banner": {"variant": str, "message": str},
}


def split(out):
    """The plain line and the spec, found as the terminal finds it: from the first brace."""
    start = out.index("{")
    return out[:start].strip(), json.loads(out[start:])


def valid(node):
    """A layout of layouts and components, each with the props its reader requires."""
    if "component" not in node:
        return node.get("layout") in ("stack", "grid") and all(valid(c) for c in node["children"])
    props = node.get("props", {})
    need = REQUIRED.get(node["component"])
    if need is None or any(not isinstance(props.get(k), t) or props.get(k) == "" for k, t in need.items()):
        return False
    if node["component"] == "performance_chart":
        series = props.get("series") or [{"values": props.get("values")}]
        return all(isinstance(p["value"], (int, float)) for s in series for p in s["values"])
    if node["component"] == "data_table":
        return all(isinstance(c.get("key"), str) and isinstance(c.get("header"), str) for c in props["columns"])
    return True


def components(node):
    if "component" in node:
        return [node]
    return [c for child in node["children"] for c in components(child)]


def by(spec, name):
    return [c["props"] for c in components(spec) if c["component"] == name]


def values(spec, name):
    return {p["label"]: p["value"] for p in by(spec, name)}


def tones(spec, name):
    return {p["label"]: p.get("direction") for p in by(spec, name)}
