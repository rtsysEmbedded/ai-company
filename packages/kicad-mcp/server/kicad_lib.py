"""KiCad S-expression reading/writing and lookups in the stock symbol and footprint libraries.

Pure standard library; runs in the MCP server process (no KiCad Python needed).
"""
import copy
import hashlib
import json
import os
import re


class KiCadError(Exception):
    """An error the agent can act on; the message is shown as the tool result."""


class Sym(str):
    """A bare (unquoted) S-expression token. Plain str values are written quoted."""

    __slots__ = ()


_TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')
_PAREN_OR_STRING = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"')
_UNESCAPE = {"n": "\n", "t": "\t", "r": "\r"}


def parse(text):
    """Parse S-expression text into nested lists of Sym (bare) and str (quoted)."""
    stack = [[]]
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token == "(":
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ")":
            if len(stack) == 1:
                raise KiCadError("Unbalanced ')' in S-expression")
            stack.pop()
        elif token[0] == '"':
            stack[-1].append(re.sub(r"\\(.)", lambda m: _UNESCAPE.get(m.group(1), m.group(1)), token[1:-1]))
        else:
            stack[-1].append(Sym(token))
    if len(stack) != 1:
        raise KiCadError("Unbalanced '(' in S-expression")
    return stack[0]


def _atom(value):
    if isinstance(value, Sym):
        return value
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def dump(node, depth=0):
    pad = "\t" * depth
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        i += 1
    head = " ".join(_atom(a) for a in node[:i])
    if i == len(node):
        return "%s(%s)" % (pad, head)
    lines = ["%s(%s" % (pad, head)]
    for item in node[i:]:
        lines.append(dump(item, depth + 1) if isinstance(item, list) else pad + "\t" + _atom(item))
    lines.append(pad + ")")
    return "\n".join(lines)


def num(value):
    """Format a number the way KiCad does: at most 4 decimals, no trailing zeros."""
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return Sym("0" if text in ("-0", "") else text)


def child(node, name):
    for item in node:
        if isinstance(item, list) and item and item[0] == name:
            return item
    return None


def children(node, name):
    return [item for item in node if isinstance(item, list) and item and item[0] == name]


def has_flag(node, name):
    """True for both `(name yes)` and the older bare `name` token."""
    flag = child(node, name)
    if flag is not None:
        return len(flag) < 2 or flag[1] != "no"
    return any(not isinstance(item, list) and item == name for item in node[1:])


def _end_of_list(text, start):
    depth = 0
    for match in _PAREN_OR_STRING.finditer(text, start):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth == 0:
                return match.end()
    raise KiCadError("Unterminated S-expression")


def split_id(lib_id, kind):
    if lib_id.count(":") != 1 or not all(lib_id.split(":")):
        raise KiCadError("%s id must look like 'Library:Name', got %r. Use search_library to find it." % (kind, lib_id))
    return lib_id.split(":")


_UNIT_SUFFIX = re.compile(r"_(\d+)_(\d+)$")


class Libraries:
    def __init__(self, symbol_dirs, footprint_dirs, cache_dir):
        self.symbol_dirs = [d for d in symbol_dirs if d and os.path.isdir(d)]
        self.footprint_dirs = [d for d in footprint_dirs if d and os.path.isdir(d)]
        self.cache_dir = cache_dir
        self._lib_text = {}
        self._symbols = {}
        self._symbol_index = None
        self._footprint_index = None

    # --- symbols ------------------------------------------------------------
    def _symbol_lib_path(self, lib):
        for directory in self.symbol_dirs:
            path = os.path.join(directory, lib + ".kicad_sym")
            if os.path.isfile(path):
                return path
        raise KiCadError("Symbol library %r not found. Use search_library with kind='symbol'." % lib)

    def _read_lib(self, lib):
        if lib not in self._lib_text:
            with open(self._symbol_lib_path(lib), "r", encoding="utf-8") as handle:
                self._lib_text[lib] = handle.read()
        return self._lib_text[lib]

    def _raw_symbol(self, lib, name):
        text = self._read_lib(lib)
        match = re.search(r'\(symbol\s+"%s"\s' % re.escape(name), text)
        if not match:
            raise KiCadError("No symbol %r in library %r. Use search_library with kind='symbol'." % (name, lib))
        return parse(text[match.start():_end_of_list(text, match.start())])[0]

    def symbol(self, lib_id):
        """Return the symbol definition with any `extends` inheritance flattened."""
        if lib_id in self._symbols:
            return self._symbols[lib_id]
        lib, name = split_id(lib_id, "Symbol")
        node = self._raw_symbol(lib, name)
        extends = child(node, "extends")
        if extends is not None:
            base_name = extends[1]
            flat = copy.deepcopy(self.symbol("%s:%s" % (lib, base_name)))
            flat[1] = name
            for unit in children(flat, "symbol"):
                unit[1] = name + unit[1][len(base_name):]
            # Like KiCad: the derived symbol's properties override the base's by name; others are inherited.
            positions = {item[1]: i for i, item in enumerate(flat) if isinstance(item, list) and item[0] == "property"}
            insert_at = max(positions.values()) + 1
            for prop in children(node, "property"):
                if prop[1] in positions:
                    flat[positions[prop[1]]] = prop
                else:
                    flat.insert(insert_at, prop)
                    insert_at += 1
            node = flat
        self._symbols[lib_id] = node
        return node

    @staticmethod
    def pins(symbol_node):
        """All pins of the normal body style as dicts, with the unit they belong to (0 = every unit)."""
        result = []
        for unit_node in children(symbol_node, "symbol"):
            suffix = _UNIT_SUFFIX.search(unit_node[1])
            unit, style = (int(suffix.group(1)), int(suffix.group(2))) if suffix else (1, 1)
            if style > 1:
                continue
            for pin in children(unit_node, "pin"):
                at = child(pin, "at")
                result.append(
                    {
                        "number": child(pin, "number")[1],
                        "name": child(pin, "name")[1],
                        "type": str(pin[1]),
                        "x": float(at[1]),
                        "y": float(at[2]),
                        "angle": float(at[3]) if len(at) > 3 else 0.0,
                        "unit": unit,
                        "hidden": has_flag(pin, "hide"),
                    }
                )
        return result

    @staticmethod
    def unit_count(symbol_node):
        units = [int(m.group(1)) for m in (_UNIT_SUFFIX.search(u[1]) for u in children(symbol_node, "symbol")) if m]
        return max(units + [1])

    @staticmethod
    def property_value(symbol_node, name):
        for prop in children(symbol_node, "property"):
            if prop[1] == name:
                return prop[2]
        return ""

    def describe_symbol(self, lib_id):
        node = self.symbol(lib_id)
        pins = self.pins(node)
        units = self.unit_count(node)
        return {
            "id": lib_id,
            "description": self.property_value(node, "Description"),
            "keywords": self.property_value(node, "ki_keywords"),
            "reference_prefix": self.property_value(node, "Reference"),
            "default_footprint": self.property_value(node, "Footprint"),
            "footprint_filters": self.property_value(node, "ki_fp_filters"),
            "datasheet": self.property_value(node, "Datasheet"),
            "is_power_symbol": child(node, "power") is not None,
            "units": units,
            "pins": [
                dict(
                    {"number": p["number"], "name": p["name"], "type": p["type"]},
                    **({"unit": p["unit"]} if units > 1 else {}),
                    **({"hidden": True} if p["hidden"] else {})
                )
                for p in sorted(pins, key=lambda p: (len(p["number"]), p["number"]))
            ],
        }

    def _load_symbol_index(self):
        if self._symbol_index is not None:
            return self._symbol_index
        files = []
        for directory in self.symbol_dirs:
            for name in sorted(os.listdir(directory)):
                if name.endswith(".kicad_sym"):
                    path = os.path.join(directory, name)
                    stat = os.stat(path)
                    files.append((path, int(stat.st_mtime), stat.st_size))
        stamp = hashlib.sha1(json.dumps(files).encode("utf-8")).hexdigest()[:16]
        cache_path = os.path.join(self.cache_dir, "symbols-%s.json" % stamp)
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as handle:
                self._symbol_index = json.load(handle)
            return self._symbol_index
        top_level = re.compile(r'\n\t\(symbol "((?:\\.|[^"\\])*)"')
        prop = re.compile(r'\(property "(Description|ki_keywords)" "((?:\\.|[^"\\])*)"')
        index = []
        for path, _, _ in files:
            lib = os.path.basename(path)[: -len(".kicad_sym")]
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            starts = list(top_level.finditer(text))
            for i, match in enumerate(starts):
                end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
                props = dict(prop.findall(text, match.end(), end))
                index.append([lib + ":" + match.group(1), props.get("Description", ""), props.get("ki_keywords", "")])
        os.makedirs(self.cache_dir, exist_ok=True)
        for stale in os.listdir(self.cache_dir):
            if stale.startswith("symbols-"):
                os.remove(os.path.join(self.cache_dir, stale))
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump(index, handle)
        self._symbol_index = index
        return index

    # --- footprints ---------------------------------------------------------
    def footprint_lib_dir(self, lib):
        for directory in self.footprint_dirs:
            path = os.path.join(directory, lib + ".pretty")
            if os.path.isdir(path):
                return path
        raise KiCadError("Footprint library %r not found. Use search_library with kind='footprint'." % lib)

    def footprint_path(self, lib_id):
        lib, name = split_id(lib_id, "Footprint")
        path = os.path.join(self.footprint_lib_dir(lib), name + ".kicad_mod")
        if not os.path.isfile(path):
            raise KiCadError("No footprint %r in library %r. Use search_library with kind='footprint'." % (name, lib))
        return path

    def _load_footprint_index(self):
        if self._footprint_index is None:
            index = []
            for directory in self.footprint_dirs:
                for lib_dir in sorted(os.listdir(directory)):
                    if lib_dir.endswith(".pretty"):
                        lib = lib_dir[: -len(".pretty")]
                        for name in sorted(os.listdir(os.path.join(directory, lib_dir))):
                            if name.endswith(".kicad_mod"):
                                index.append([lib + ":" + name[: -len(".kicad_mod")], "", ""])
            self._footprint_index = index
        return self._footprint_index

    def describe_footprint(self, lib_id):
        with open(self.footprint_path(lib_id), "r", encoding="utf-8") as handle:
            node = parse(handle.read())[0]
        pads = []
        for pad in children(node, "pad"):
            at, size, drill = child(pad, "at"), child(pad, "size"), child(pad, "drill")
            entry = {
                "number": pad[1],
                "type": str(pad[2]),
                "shape": str(pad[3]),
                "at": [float(at[1]), float(at[2])],
                "size": [float(size[1]), float(size[2])],
            }
            if drill is not None:
                entry["drill"] = next((float(v) for v in drill[1:] if not isinstance(v, list) and v != "oval"), None)
            pads.append(entry)
        xs = [p["at"][0] + s * p["size"][0] / 2 for p in pads for s in (-1, 1)] or [0]
        ys = [p["at"][1] + s * p["size"][1] / 2 for p in pads for s in (-1, 1)] or [0]
        attr = child(node, "attr")
        return {
            "id": lib_id,
            "description": (child(node, "descr") or ["", ""])[1],
            "tags": (child(node, "tags") or ["", ""])[1],
            "mount": str(attr[1]) if attr and len(attr) > 1 else "unspecified",
            "pad_count": len(pads),
            "pad_numbers": sorted({p["number"] for p in pads if p["number"]}, key=lambda n: (len(n), n)),
            "pad_extent_mm": [round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)],
            "pads": pads[:80],
            "note": "Pad positions are relative to the footprint origin, in mm, Y pointing down."
            + (" Only the first 80 pads are listed." if len(pads) > 80 else ""),
        }

    # --- search -------------------------------------------------------------
    def search(self, kind, query, limit=25):
        if kind == "symbol":
            index = self._load_symbol_index()
        elif kind == "footprint":
            index = self._load_footprint_index()
        else:
            raise KiCadError("kind must be 'symbol' or 'footprint'")
        words = [w for w in re.split(r"[\s,]+", query.lower()) if w]
        if not words:
            raise KiCadError("query must not be empty")
        scored = []
        for lib_id, description, keywords in index:
            lowered = lib_id.lower()
            name = lowered.split(":", 1)[1]
            haystack = " ".join((lowered, description.lower(), keywords.lower()))
            if not all(w in haystack for w in words):
                continue
            joined = " ".join(words)
            if name == joined or lowered == joined:
                rank = 0
            elif all(w in name for w in words):
                rank = 1 if name.startswith(words[0]) else 2
            elif all(w in lowered for w in words):
                rank = 3
            else:
                rank = 4
            scored.append((rank, len(name), lib_id, description))
        scored.sort()
        results = [dict({"id": lib_id}, **({"description": d} if d else {})) for _, _, lib_id, d in scored[:limit]]
        return {"kind": kind, "query": query, "total_matches": len(scored), "results": results}
