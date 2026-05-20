"""
Per-language builtin sets — excluded from call graph edges.

These are standard library functions, common method names, and framework
noise that appear in almost every function body and add no signal to the
call graph (e.g. "len", "print", "push", "resolve").
"""

TS_BUILTINS: frozenset[str] = frozenset({
    # console
    "log", "warn", "error", "info", "debug", "trace", "console",
    # type constructors / globals
    "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "encodeURI", "decodeURI", "eval",
    "Promise", "resolve", "reject", "then", "catch", "finally", "all", "race",
    "JSON", "stringify", "parse",
    "Object", "Array", "String", "Number", "Boolean", "Symbol", "BigInt",
    "Math", "Date", "RegExp", "Error", "Map", "Set", "WeakMap", "WeakSet",
    # array methods
    "push", "pop", "shift", "unshift", "splice", "slice", "concat",
    "map", "filter", "reduce", "reduceRight", "forEach", "find", "findIndex",
    "some", "every", "includes", "indexOf", "lastIndexOf", "flat", "flatMap",
    "sort", "reverse", "fill", "copyWithin", "entries", "keys", "values",
    "join", "from", "of", "isArray",
    # string methods
    "trim", "trimStart", "trimEnd", "replace", "replaceAll", "split",
    "startsWith", "endsWith", "padStart", "padEnd", "repeat", "charAt",
    "charCodeAt", "substring", "substr", "toLowerCase", "toUpperCase",
    "toString", "valueOf", "toFixed", "toLocaleString",
    # object methods
    "hasOwnProperty", "assign", "freeze", "create", "defineProperty",
    "getOwnPropertyNames", "getPrototypeOf", "entries", "fromEntries",
    # async / timers
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame", "queueMicrotask",
    # module
    "require", "exports", "super", "constructor",
    # misc
    "get", "set", "has", "delete", "clear", "size",
    "next", "return", "throw", "done",
})

PY_BUILTINS: frozenset[str] = frozenset({
    # builtins
    "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
    "reversed", "list", "dict", "set", "frozenset", "tuple",
    "str", "int", "float", "bool", "bytes", "bytearray", "complex",
    "type", "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "open", "input", "repr", "vars", "dir", "id", "hash",
    "abs", "min", "max", "sum", "round", "divmod", "pow",
    "any", "all", "next", "iter", "callable",
    "chr", "ord", "hex", "oct", "bin", "format",
    "super", "object", "property", "staticmethod", "classmethod",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "NotImplementedError",
    "OSError", "IOError", "FileNotFoundError", "PermissionError",
    # common methods (list/dict/str)
    "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse",
    "get", "update", "items", "keys", "values", "setdefault",
    "add", "discard", "union", "intersection", "difference",
    "join", "split", "rsplit", "strip", "lstrip", "rstrip",
    "replace", "find", "rfind", "index", "rindex", "count",
    "startswith", "endswith", "lower", "upper", "capitalize", "title",
    "encode", "decode", "format", "format_map",
    "read", "write", "close", "seek", "tell", "flush",
    "copy", "deepcopy",
    # dunder
    "__init__", "__str__", "__repr__", "__len__", "__getitem__", "__setitem__",
    "__delitem__", "__contains__", "__iter__", "__next__", "__enter__", "__exit__",
})

GO_BUILTINS: frozenset[str] = frozenset({
    # builtin functions
    "make", "new", "len", "cap", "append", "copy", "delete", "close",
    "panic", "recover", "print", "println",
    # fmt package (very common)
    "Println", "Printf", "Sprintf", "Fprintf", "Errorf", "Scanf",
    "Print", "Sprint", "Sscanf", "Ssprintf",
    # errors
    "Error", "New", "Is", "As", "Unwrap",
    # common method names
    "String", "Error", "Close", "Read", "Write", "Len", "Cap",
    "Lock", "Unlock", "RLock", "RUnlock",
    "Get", "Set", "Add", "Remove", "Delete", "Clear", "Reset",
    "Marshal", "Unmarshal", "Encode", "Decode",
    "Open", "Create", "Stat", "Remove",
    # context
    "Background", "TODO", "WithCancel", "WithTimeout", "WithDeadline",
    "Done", "Err", "Value", "Cancel",
})
