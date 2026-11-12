"""Parser zrzutu mysqldump (CREATE TABLE + INSERT) do podglądu bazy bez uruchomionego MySQL-a."""
import re

CREATE_RE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?`([^`]+)`\s*\((.*?)\n\)\s*[^;]*;", re.S)
INSERT_RE = re.compile(r"INSERT\s+(?:IGNORE\s+)?INTO\s+`([^`]+)`\s*(?:\(([^)]*)\))?\s*VALUES\s*", re.I)
ESC = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "b": "\b", "Z": "\x1a"}
MAX_ROWS = 20000     # tyle wierszy na tabelę wczytujemy do podglądu


def _scan_type(s):
    """Typ kolumny do pierwszej spacji spoza nawiasów i cudzysłowów (np. enum('a','b'), decimal(10,2))."""
    depth, q, i = 0, None, 0
    while i < len(s):
        ch = s[i]
        if q:
            if ch == "\\": i += 2; continue
            if ch == q: q = None
        elif ch in "'\"": q = ch
        elif ch == "(": depth += 1
        elif ch == ")": depth -= 1
        elif ch.isspace() and depth == 0: break
        i += 1
    return s[:i], s[i:].strip()


def _parse_column(line):
    m = re.match(r"`([^`]+)`\s+(.*)$", line)
    name, rest = m.group(1), m.group(2)
    typ, rest = _scan_type(rest)
    for suffix in ("unsigned", "zerofill"):
        while rest.lower().startswith(suffix):
            typ += " " + suffix
            rest = rest[len(suffix):].strip()
    dm = re.search(r"DEFAULT\s+('(?:[^'\\]|\\.|'')*'|[^\s,]+(?:\(\))?)", rest, re.I)
    return {"name": name, "type": typ, "nullable": "NOT NULL" not in rest.upper(),
            "auto": "AUTO_INCREMENT" in rest.upper(), "default": dm.group(1) if dm else None,
            "pk": False, "unique": False, "fk": None}


def _cols(text):
    return re.findall(r"`([^`]+)`", text)


def parse_schema(text):
    tables = []
    for m in CREATE_RE.finditer(text):
        t = {"name": m.group(1), "columns": [], "indexes": [], "fks": [], "rows": 0}
        pk = []
        for raw in m.group(2).split("\n"):
            line = raw.strip().rstrip(",").strip()
            if not line: continue
            up = line.upper()
            if up.startswith("PRIMARY KEY"):
                pk = _cols(line.split("(", 1)[1])
            elif up.startswith(("UNIQUE KEY", "UNIQUE INDEX", "KEY", "INDEX", "FULLTEXT KEY", "SPATIAL KEY")):
                names = _cols(line)
                t["indexes"].append({"name": names[0], "columns": names[1:], "unique": up.startswith("UNIQUE")})
            elif up.startswith("CONSTRAINT") and "FOREIGN KEY" in up:
                fm = re.search(r"CONSTRAINT `([^`]+)` FOREIGN KEY \(([^)]*)\) REFERENCES `([^`]+)` \(([^)]*)\)(.*)", line, re.I)
                if fm:
                    od = re.search(r"ON DELETE (CASCADE|SET NULL|SET DEFAULT|NO ACTION|RESTRICT)", fm.group(5), re.I)
                    ou = re.search(r"ON UPDATE (CASCADE|SET NULL|SET DEFAULT|NO ACTION|RESTRICT)", fm.group(5), re.I)
                    t["fks"].append({"name": fm.group(1), "columns": _cols(fm.group(2)), "ref_table": fm.group(3),
                                     "ref_columns": _cols(fm.group(4)),
                                     "on_delete": od.group(1).upper() if od else "RESTRICT",
                                     "on_update": ou.group(1).upper() if ou else "RESTRICT"})
            elif line.startswith("`"):
                t["columns"].append(_parse_column(line))
        by = {c["name"]: c for c in t["columns"]}
        for c in pk:
            if c in by: by[c]["pk"] = True
        for ix in t["indexes"]:
            if ix["unique"] and len(ix["columns"]) == 1 and ix["columns"][0] in by: by[ix["columns"][0]]["unique"] = True
        for fk in t["fks"]:
            for col, ref in zip(fk["columns"], fk["ref_columns"]):
                if col in by: by[col]["fk"] = {"table": fk["ref_table"], "column": ref}
        tables.append(t)
    return tables


def _parse_string(s, i):
    """s[i] == "'" -> (tekst, indeks po zamykającym cudzysłowie)."""
    out, i = [], i + 1
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            out.append(ESC.get(s[i + 1], s[i + 1])); i += 2
        elif ch == "'":
            if i + 1 < n and s[i + 1] == "'": out.append("'"); i += 2
            else: return "".join(out), i + 1
        else:
            j = i
            while j < n and s[j] not in "\\'": j += 1
            out.append(s[i:j]); i = j
    return "".join(out), i


def _parse_value(s, i):
    ch = s[i]
    if ch == "'":
        return _parse_string(s, i)
    if ch == "_":                       # _binary 'xxx', _utf8mb4 'xxx'
        j = i
        while j < len(s) and not s[j].isspace(): j += 1
        while j < len(s) and s[j].isspace(): j += 1
        if j < len(s) and s[j] == "'": return _parse_string(s, j)
    j = i
    while j < len(s) and s[j] not in ",)": j += 1
    tok = s[i:j].strip()
    if tok.lower() == "null": return None, j
    if re.fullmatch(r"-?\d+", tok): return int(tok), j
    return tok, j                       # liczby dziesiętne jako tekst (bez utraty zapisu), 0xABCD, b'101', funkcje


def _parse_tuples(s, i, limit):
    rows, n = [], len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n,": i += 1
        if i >= n or s[i] == ";": return rows, i + 1, False
        if s[i] != "(": return rows, i, False
        i += 1
        row = []
        while i < n:
            while i < n and s[i].isspace(): i += 1
            val, i = _parse_value(s, i)
            row.append(val)
            while i < n and s[i].isspace(): i += 1
            if i < n and s[i] == ",": i += 1; continue
            if i < n and s[i] == ")": i += 1; break
            break
        rows.append(row)
        if len(rows) >= limit:
            end = s.find(";\n", i)
            return rows, (end + 2 if end != -1 else n), True
    return rows, i, False


def parse_dump(text):
    tables = parse_schema(text)
    cols = {t["name"]: [c["name"] for c in t["columns"]] for t in tables}
    rows, truncated, pos = {t["name"]: [] for t in tables}, set(), 0
    while True:
        m = INSERT_RE.search(text, pos)
        if not m: break
        name = m.group(1)
        got, pos, cut = _parse_tuples(text, m.end(), max(1, MAX_ROWS - len(rows.get(name, []))))
        if name not in rows: continue
        listed = _cols(m.group(2)) if m.group(2) else None
        if listed and listed != cols[name]:     # INSERT z listą kolumn: układamy wartości według CREATE TABLE
            idx = {c: k for k, c in enumerate(listed)}
            got = [[r[idx[c]] if c in idx and idx[c] < len(r) else None for c in cols[name]] for r in got]
        rows[name].extend(got)
        if cut or len(rows[name]) >= MAX_ROWS: truncated.add(name)
    for t in tables:
        t["rows"] = len(rows[t["name"]])
        t["truncated"] = t["name"] in truncated
    return {"tables": tables, "rows": rows}
