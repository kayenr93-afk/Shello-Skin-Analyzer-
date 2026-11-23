// Shared validator for POST /api/events payloads. Mirrors the Python
// implementation in server.py; both read api/_lib/event_schema.json.

const fs = require("fs");
const path = require("path");

const SCHEMA = JSON.parse(
  fs.readFileSync(path.join(__dirname, "event_schema.json"), "utf8")
);
const FORBIDDEN = new RegExp(SCHEMA.forbidden_string_regex, "i");

function checkString(s) {
  if (s.length > SCHEMA.max_string_chars) return "string too long";
  if (FORBIDDEN.test(s)) return "forbidden string content";
  return null;
}

function checkField(value, spec, keyPath) {
  const t = spec.t;
  if (value === null || value === undefined) return null; // optional
  switch (t) {
    case "str":
      if (typeof value !== "string") return `${keyPath}: expected string`;
      return checkString(value);
    case "int":
      if (!Number.isInteger(value)) return `${keyPath}: expected int`;
      return null;
    case "num":
      if (typeof value !== "number" || !Number.isFinite(value)) return `${keyPath}: expected number`;
      return null;
    case "bool":
      if (typeof value !== "boolean") return `${keyPath}: expected bool`;
      return null;
    case "str[]":
    case "int[]": {
      if (!Array.isArray(value)) return `${keyPath}: expected array`;
      if (value.length > SCHEMA.max_array_items) return `${keyPath}: array too long`;
      const isStr = t === "str[]";
      for (const item of value) {
        if (isStr) {
          if (typeof item !== "string") return `${keyPath}: expected string item`;
          const bad = checkString(item);
          if (bad) return `${keyPath}: ${bad}`;
        } else if (!Number.isInteger(item)) {
          return `${keyPath}: expected int item`;
        }
      }
      return null;
    }
    case "obj": {
      if (typeof value !== "object" || Array.isArray(value)) return `${keyPath}: expected object`;
      for (const k of Object.keys(value)) {
        if (!(k in (spec.fields || {}))) return `${keyPath}: unknown key ${k}`;
      }
      for (const [k, sub] of Object.entries(spec.fields || {})) {
        const err =
          checkField(value[k], sub, `${keyPath}.${k}`) ||
          checkEnum(value[k], sub, `${keyPath}.${k}`);
        if (err) return err;
      }
      return null;
    }
    default:
      return `${keyPath}: unknown field type ${t}`;
  }
}

function checkEnum(value, spec, keyPath) {
  if (spec.enum && value != null && !spec.enum.includes(value)) {
    return `${keyPath}: value not allowed`;
  }
  return null;
}

// Returns null when valid, an error string otherwise.
function validateEvent(type, payload) {
  const tspec = SCHEMA.types[type];
  if (!tspec) return "unknown event type";
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return "payload must be an object";
  }
  const fields = tspec.fields || {};
  for (const k of Object.keys(payload)) {
    if (!(k in fields)) return `unknown key ${k}`;
  }
  for (const req of tspec.required || []) {
    if (payload[req] === undefined || payload[req] === null) return `missing ${req}`;
  }
  for (const [k, spec] of Object.entries(fields)) {
    const err = checkField(payload[k], spec, k) || checkEnum(payload[k], spec, k);
    if (err) return err;
  }
  return null;
}

module.exports = { SCHEMA, validateEvent };
