# Copyright Exbee Ltd 2026
# License Apache 2
"""
tlbTemplate-dnb.py -- DNB DPM dictionary and annotated templates to a compiled Tavi model

Purpose
-------
Sibling of ``tlbTemplate-eiopa.py``, for De Nederlandsche Bank's DPM workbooks (here the
Macroeconomic Statistics reporting, MES 5.0.0).  The DNB workbook is dictionary-driven: its
``Data Points`` sheet resolves every template cell to its metric and full dimensional context,
and its ``Dict …`` sheets name the metrics, dimensions, domains and members.  The ``T…``
template sheets add the presentation: column and row headers and the ordinate codes.

The output is one compiled model (``https://xbrl.org/2026/compiled``) that keeps DNB's own names
-- ``dnb_met:mi004``, ``dnb_dim:dim081``, ``dnb_dom050:mem001`` -- in DNB's namespaces, rather than
folding them into one module namespace as the EIOPA generator does.  With DNB's names, DNB's own
xBRL-CSV reports are read by the model's fact sources directly (see ``dnbSampleReport.py``), with
no conversion of the report.  Per template table it has:

* a ``gridLayout`` data table: x and y gridHeaders from the template sheet (column headers from
  its header band, merged cells as spans; row headers from the labels' indentation); axis items
  carrying the dimensions common to a column (x) or row (y), the dimensions common to the whole
  table on a single z axis item, and ``tablePoints`` for what is particular to a cell.  An open
  table's key columns (``D01`` …) are x axis groups of their dimension, each with a network of the
  hierarchy the template names for it (the rows a key column accepts);
* a report cube (a concept domain of the table's metrics, its dimensions optional), with one
  negative cube per cell the workbook marks invalid (``Cell Validity`` false), which the cube
  excludes;
* a ``tableTemplate`` and ``factMap`` matching DNB's xBRL-CSV table files: a closed table has one
  column per cell, named ``R{row}_C{column}``; an open table has key columns ``YD01`` … and value
  columns ``C{column}``, whose key dimensions take the key columns' values (``"$YD01"``).

Namespaces
----------
DNB's dimension namespace is in the workbook (``http://www.dnb.nl/xbrl/dict/dim``).  The others
follow the same DPM pattern and are **inferred**: ``…/dict/met``, ``…/dict/typ``,
``…/dict/dom/domNNN``, and ``http://www.dnb.nl/xbrl/fws/mes/{version}`` for the tables, cubes and
templates this generator names.  The model is self-contained, so they only have to be consistent.

Usage
-----
::

    python3 tlbTemplate-dnb.py <workbook.xlsx> <model.json> [<table code> ...]
"""
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict

from openpyxl import load_workbook

DNB = "http://www.dnb.nl/xbrl"
DATA_TYPES = {"monetaryItemType": "xbrlr:monetary", "percentItemType": "xbrlr:percent",
              "integerItemType": "xs:integer", "stringItemType": "xs:string",
              "enumerationItemType": "xs:QName", "dateItemType": "xs:date",
              "booleanItemType": "xs:boolean", "decimalItemType": "xs:decimal"}
UNITS = {"uEUR": "iso4217:EUR"}            # uPURE and no unit: a fact without a unit
CORE = {"xbrl:concept", "xbrl:period", "xbrl:entity", "xbrl:unit", "xbrl:language"}

# Data Points columns, by position: the sheet's header row is offset from its data after
# column 7, so it is read by position rather than by heading.
DP_TABLE, DP_KEYS, DP_X, DP_Y, DP_VALID, DP_TYPE, DP_UNIT, DP_DECIMALS, DP_METRIC, DP_CONTEXT = \
    1, 3, 4, 5, 7, 8, 9, 10, 16, 17


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__.split("Usage\n-----\n::\n")[1].strip())
    workbookPath, outPath, onlyTables = argv[1], argv[2], set(argv[3:])
    wb = load_workbook(workbookPath)
    m = re.search(r"(\d+)-(\d+)-(\d+)\.xlsx$", workbookPath)   # dnb-mes-…-5-0-0.xlsx
    version = ".".join(m.groups()) if m else "unknown"
    fws = "dnb_mes"
    namespaces = OrderedDict([
        (fws, f"{DNB}/fws/mes/{version}"),
        ("dnb_met", f"{DNB}/dict/met"), ("dnb_dim", f"{DNB}/dict/dim"), ("dnb_typ", f"{DNB}/dict/typ"),
        ("iso4217", "http://www.xbrl.org/2003/iso4217"), ("lei", "http://standards.iso.org/iso/17442"),
    ])
    labels = OrderedDict()                 # object name -> English label

    def rows(sheet):
        it = wb[sheet].iter_rows(values_only=True)
        next(it)
        return [r for r in it if any(c not in (None, "") for c in r)]

    # ---- dictionary ----
    dimensions = OrderedDict()             # dnb_dim:dim081 -> domain (dnb_dom050 / dnb_typ005)
    for r in rows("Dict Dimensions"):
        if r[1] and r[3]:
            name = "dnb_dim:" + r[1].split("_", 1)[1]
            dimensions[name] = r[3]
            labels[name] = r[7] or r[6]
    typedDomains = {r[1]: r[3] for r in rows("Dict Typed Domains") if r[1]}
    for r in rows("Dict Explicit Domains"):
        if r[1]:
            namespaces.setdefault(r[1], f"{DNB}/dict/dom/{r[1].split('_', 1)[1]}")
    memberLabels = {}
    domainMembers = defaultdict(list)       # dnb_dom050 -> [dnb_dom050:mem001, …]
    for r in rows("Dict Domain Members"):
        if r[1] and r[3]:
            namespaces.setdefault(r[1], f"{DNB}/dict/dom/{r[1].split('_', 1)[1]}")
            member = f"{r[1]}:{r[3].split('_', 1)[1]}"
            memberLabels[member] = r[7] or r[6]
            domainMembers[r[1]].append(member)
    hierarchies = defaultdict(list)        # (dnb_dom050, hierarchy label) -> [member, …]
    hierarchyLabel = {}                    # hierarchy role -> its label
    for r in rows("Dict Hierarchies"):
        if r[1] and r[5]:
            hierarchyLabel[r[1]] = r[5].strip()
        elif r[1] and r[10] and r[14] and r[1] in hierarchyLabel:
            hierarchies[(r[10], hierarchyLabel[r[1]])].append(f"{r[10]}:{r[14].split('_', 1)[1]}")
    metrics = {}                           # dnb_met:mi004 -> (dataType, label)
    for r in rows("Dict Metrics"):
        if r[1] and r[5]:
            metrics["dnb_met:" + r[1].split("_", 1)[1]] = (DATA_TYPES.get(r[5].rpartition(":")[2], "xs:string"),
                                                           r[9] or r[8])
    dimByLabel = defaultdict(list)
    for d, lbl in ((d, labels.get(d)) for d in dimensions):
        if lbl:
            dimByLabel[lbl.strip().lower()].append(d)

    def isTyped(dim):
        return dimensions.get(dim, "").startswith("dnb_typ")

    # ---- Data Points: each table's cells ----
    cellsOf = defaultdict(list)
    for r in rows("Data Points"):
        table = r[DP_TABLE]
        if not table or (onlyTables and table not in onlyTables):
            continue
        dims = OrderedDict()
        metric = (r[DP_METRIC] or "").split(" ")[0]
        if metric:
            dims["xbrl:concept"] = metric
        for part in (r[DP_CONTEXT] or "").split("|"):
            tokens = part.split()
            if len(tokens) >= 2:
                dim = "dnb_dim:" + tokens[0]
                dims[dim] = None if tokens[1].startswith("typ") or len(tokens) < 3 else f"dnb_{tokens[1]}:{tokens[2]}"
        cellsOf[table].append({
            "x": str(r[DP_X]), "y": str(r[DP_Y]), "keys": (r[DP_KEYS] or "").split(),
            "valid": str(r[DP_VALID]).lower() == "true", "dims": dims,
            "unit": UNITS.get(r[DP_UNIT]), "decimals": r[DP_DECIMALS],
        })

    dataTables, cubes, domainNetworks, tableTemplates, factMaps = [], [], [], [], []
    conceptsUsed, membersUsed, dimsUsed = OrderedDict(), set(), OrderedDict()
    singleMemberDomains = {}
    problems = []

    def singleMemberDomain(dim, member):
        key = (dim, member)
        if key not in singleMemberDomains:
            name = f"{fws}:excl{len(singleMemberDomains) + 1}Dom"
            root = "xbrl:conceptDomain" if dim == "xbrl:concept" else f"{dim}Cls"
            domainNetworks.append({"name": name, "root": root, "relationships": [{"source": root, "target": member}]})
            singleMemberDomains[key] = name
        return singleMemberDomains[key]

    for table, cells in cellsOf.items():
        if table not in wb.sheetnames:
            problems.append(f"{table}: no template sheet")
            continue
        sheet = layoutOfSheet(wb[table])
        if sheet is None:
            problems.append(f"{table}: no ordinate row found")
            continue
        qnTable = f"{fws}:{table}"
        labels[qnTable] = sheet["title"]
        isOpen = any(c["keys"] for c in cells)
        valid = [c for c in cells if c["valid"]]
        for c in valid:
            conceptsUsed.setdefault(c["dims"].get("xbrl:concept"), c)
        keyCodes = [x for x in sheet["xCodes"] if x.startswith("D")]

        # an open table's key columns: the dimension each names in the sheet's Hierarchy row
        keyDims = OrderedDict()
        for code in keyCodes:
            text = (sheet["keyHeaders"].get(code) or "").split("\n")[0].strip().lower()
            candidates = [d for d in dimByLabel.get(text, ()) if any(d in c["dims"] for c in valid)]
            if len(candidates) == 1:
                keyDims[code] = candidates[0]
        # a typed key dimension has no hierarchy: it is the one the cells' context gives without a member
        typedInContext = list(OrderedDict.fromkeys(d for c in valid for d, v in c["dims"].items()
                                                   if v is None and isTyped(d) and d not in keyDims.values()))
        for code in keyCodes:
            if code not in keyDims and typedInContext:
                keyDims[code] = typedInContext.pop(0)
        keyDims = OrderedDict((code, keyDims[code]) for code in keyCodes if code in keyDims)
        for code in keyCodes:
            if code not in keyDims:
                problems.append(f"{table}: key column {code} has no dimension")
        keyDimSet = set(keyDims.values())
        for d in keyDimSet:                 # a key column holds any member of its dimension's domain
            if not isTyped(d):
                membersUsed.update(domainMembers.get(dimensions.get(d), ()))

        def common(cellList):
            if not cellList:
                return OrderedDict()
            first = cellList[0]["dims"]
            return OrderedDict((d, v) for d, v in first.items()
                               if d not in keyDimSet and v is not None and all(c["dims"].get(d) == v for c in cellList[1:]))

        tableCommon = common(valid)
        # Each other dimension has a home axis: x when it is constant down every column (over its
        # valid cells) and differs between columns, else y when the same holds for rows; what is
        # neither is particular to cells (tablePoints).  Deciding per dimension rather than per
        # row or column matters: a row with one valid cell would otherwise take that cell's column
        # dimensions too, and every cell of the row would then be the same data point.
        def constantAlong(d, ordinate, codes):
            values = set()
            for code in codes:
                inLine = {c["dims"].get(d) for c in valid if c[ordinate] == code}
                if len(inLine) > 1:
                    return False
                values |= inLine
            return len(values) > 1
        otherDims = OrderedDict.fromkeys(d for c in valid for d, v in c["dims"].items()
                                         if v is not None and d not in tableCommon and d not in keyDimSet)
        xDims = [d for d in otherDims if constantAlong(d, "x", sheet["xCodes"])]
        yDims = [d for d in otherDims if d not in xDims and not isOpen and constantAlong(d, "y", sheet["yCodes"])]

        def lineDims(ordinate, code, dims):
            cellsOnLine = [c for c in valid if c[ordinate] == code]
            return OrderedDict((d, cellsOnLine[0]["dims"][d]) for d in dims
                               if cellsOnLine and cellsOnLine[0]["dims"].get(d) is not None)
        xCommon = {x: lineDims("x", x, xDims) for x in sheet["xCodes"]}
        yCommon = {y: lineDims("y", y, yDims) for y in sheet["yCodes"]} if not isOpen else {}

        xItems = [{"axisId": ("C" + x) if not x.startswith("D") else x, "dimensions": dict(xCommon.get(x) or {})}
                  for x in sheet["xCodes"]]
        xAxis = {"gridHeaders": sheet["xHeaders"], "gridAxis": {"axisItems": xItems}} if sheet["xHeaders"] else \
                {"gridAxis": {"axisItems": xItems}}
        if keyDims:
            # a key column holds members of the hierarchy its Hierarchy cell names ("Listed shares
            # {…}"): the axis group's network, so another template's rows are not this one's
            groups = []
            for code, d in keyDims.items():
                group = {"dimensionName": d, "axisIds": [code]}
                hierarchy = (sheet["keyHeaders"].get(code) or "").split("\n", 1)[-1].split(" {")[0].strip()
                members = hierarchies.get((dimensions.get(d), hierarchy)) if not isTyped(d) else None
                if members:
                    networkName = f"{qnTable}_{code}_Hierarchy"
                    root = f"{d}Cls"
                    domainNetworks.append({"name": networkName, "root": root,
                                           "relationships": [{"source": root, "target": m} for m in members]})
                    group["axisNetwork"] = networkName
                    membersUsed.update(members)
                elif not isTyped(d):
                    problems.append(f"{table}: key column {code} hierarchy {hierarchy!r} not found")
                groups.append(group)
            xAxis["gridAxis"]["axisGroups"] = groups
        if isOpen:
            yAxis = {"gridAxis": {}}
        else:
            yItems = [{"axisId": "R" + y, "dimensions": dict(yCommon.get(y) or {})} for y in sheet["yCodes"]]
            yAxis = {"gridAxis": {"axisItems": yItems}}
            if sheet["yHeaders"]:
                yAxis["gridHeaders"] = sheet["yHeaders"]
        dataTable = OrderedDict([("name", qnTable), ("tableType", "gridLayout"), ("cubeName", f"{qnTable}_Cube"),
                                 ("xAxis", xAxis), ("yAxis", yAxis)])
        if tableCommon:
            dataTable["zAxis"] = {"gridAxis": {"axisItems": [{"dimensions": dict(tableCommon)}]}}

        # what is particular to a cell (closed tables): tablePoints at its grid coordinates
        tablePoints = []
        if not isOpen:
            for c in valid:
                if c["x"] not in sheet["xCodes"] or c["y"] not in sheet["yCodes"]:
                    problems.append(f"{table}: cell {c['x']}/{c['y']} has no ordinate in the sheet")
                    continue
                composite = {**tableCommon, **(xCommon.get(c["x"]) or {}), **(yCommon.get(c["y"]) or {})}
                extra = {d: v for d, v in c["dims"].items() if v is not None and composite.get(d) != v}
                if extra:
                    tablePoints.append({"gridCoordinates": {"xAxis": sheet["xCodes"].index(c["x"]) + 1,
                                                            "yAxis": sheet["yCodes"].index(c["y"]) + 1},
                                        "dimensions": extra})
        if tablePoints:
            dataTable["tablePoints"] = tablePoints
        dataTables.append(dataTable)

        # the cube, and a negative cube for each invalid cell
        tableDims = OrderedDict()
        for c in cells:
            for d in c["dims"]:
                if d != "xbrl:concept":
                    tableDims[d] = True
        for d in keyDimSet:
            tableDims[d] = True
        conceptDom = f"{qnTable}_ConceptDom"
        tableConcepts = list(OrderedDict.fromkeys(c["dims"]["xbrl:concept"] for c in valid if "xbrl:concept" in c["dims"]))
        domainNetworks.append({"name": conceptDom, "root": "xbrl:conceptDomain",
                               "relationships": [{"source": "xbrl:conceptDomain", "target": m} for m in tableConcepts]})
        cubeDims = [{"dimension": "xbrl:concept", "domainNetwork": conceptDom}]
        for d in tableDims:
            dimsUsed[d] = True
            cubeDims.append({"dimension": d, "domainDataType": typedDomains.get(dimensions.get(d), "xs:string"), "optional": True}
                            if isTyped(d) else {"dimension": d, "optional": True})
        cubeDims += [{"dimension": "xbrl:period"}, {"dimension": "xbrl:entity"}, {"dimension": "xbrl:unit", "optional": True}]
        cube = {"name": f"{qnTable}_Cube", "cubeType": "xbrl:reportCube", "cubeDimensions": cubeDims}
        excludes = []
        for c in cells:
            if c["valid"] or isOpen or not c["dims"].get("xbrl:concept"):
                continue
            exclName = f"{qnTable}_Excl{len(excludes) + 1}"
            exclDims = [{"dimension": d, "domainNetwork": singleMemberDomain(d, v)} for d, v in c["dims"].items() if v is not None]
            exclDims += [{"dimension": "xbrl:period"}, {"dimension": "xbrl:entity"}]
            cubes.append({"name": exclName, "cubeType": "xbrl:negativeCube", "cubeDimensions": exclDims})
            excludes.append(exclName)
            membersUsed.update(v for d, v in c["dims"].items() if d != "xbrl:concept" and v)
        if excludes:
            cube["excludeCubes"] = excludes
        cubes.append(cube)

        # the table template matching DNB's xBRL-CSV table file
        columns = []
        if isOpen:
            columns += [{"columnName": "Y" + code} for code in keyCodes]
        for c in sorted(valid, key=lambda c: (sheet["yCodes"].index(c["y"]) if c["y"] in sheet["yCodes"] else 0,
                                              sheet["xCodes"].index(c["x"]) if c["x"] in sheet["xCodes"] else 0)):
            fd = OrderedDict((d, v) for d, v in c["dims"].items() if v is not None and d not in keyDimSet)
            for code, d in keyDims.items():
                fd[d] = "$Y" + code
            if c["unit"]:
                fd["xbrl:unit"] = c["unit"]
            column = {"columnName": f"C{c['x']}" if isOpen else f"R{c['y']}_C{c['x']}", "factDimensions": fd}
            if c["decimals"] is not None and str(c["decimals"]).lstrip("-").isdigit():
                column["decimals"] = int(c["decimals"])
            columns.append(column)
            membersUsed.update(v for d, v in fd.items() if d not in CORE and v and not str(v).startswith("$"))
        tableTemplates.append({"name": f"{qnTable}_Template", "columns": columns})
        factMaps.append({"name": f"{qnTable}_FactMap", "templateName": f"{qnTable}_Template"})
        for item in xItems + (yAxis["gridAxis"].get("axisItems") or []):
            membersUsed.update(v for d, v in item["dimensions"].items() if d != "xbrl:concept")
        membersUsed.update(v for d, v in tableCommon.items() if d != "xbrl:concept")
        for tp in tablePoints:
            membersUsed.update(v for d, v in tp["dimensions"].items() if d != "xbrl:concept")

    # ---- the model ----
    concepts = []
    for name in conceptsUsed:
        if not name:
            continue
        dataType, label = metrics.get(name, ("xs:string", None))
        concepts.append({"name": name, "dataType": dataType, "periodType": "instant"})
        if label:
            labels[name] = label
    usedDims = [d for d in dimensions if d in dimsUsed]
    modelDims = [{"name": d, "domainClass": f"{d}Cls"} for d in usedDims]
    domainClasses = [{"name": f"{d}Cls", "allowedDomainItem": typedDomains.get(dimensions[d], "xs:string") if isTyped(d)
                      else "xbrl:memberObject"} for d in usedDims]
    members = [{"name": m} for m in sorted(membersUsed)]
    for m in membersUsed:
        if memberLabels.get(m):
            labels[m] = memberLabels[m]
    usedPrefixes = {n.partition(":")[0] for n in list(membersUsed) + usedDims + [c["name"] for c in concepts]} | {fws, "iso4217", "lei"}
    oim = OrderedDict()
    oim["documentInfo"] = OrderedDict([
        ("documentType", "https://xbrl.org/2026/compiled"),
        ("namespaces", OrderedDict((p, u) for p, u in namespaces.items() if p in usedPrefixes)),
    ])
    model = oim["xbrlModel"] = OrderedDict()
    model["name"] = f"{fws}:MES"
    model["concepts"] = concepts
    model["dimensions"] = modelDims
    model["domainClasses"] = domainClasses
    model["members"] = members
    model["domainNetworks"] = domainNetworks
    model["cubes"] = cubes
    model["labels"] = [{"forObject": n, "labelType": "xbrl:label", "language": "en", "value": v}
                       for n, v in labels.items()
                       if v and (n in membersUsed or n in dimsUsed or n in conceptsUsed or n.startswith(fws + ":"))]
    model["tableTemplates"] = tableTemplates
    model["factMaps"] = factMaps
    model["layouts"] = [{"name": f"{fws}:MESLayout", "tableConstruction": "topDown", "dataTables": dataTables}]
    inlineBaseSpecObjects(oim)
    with open(outPath, "w", encoding="utf-8") as fh:
        json.dump(oim, fh, indent=1)
    print(f"tables {len(dataTables)}, cubes {len(cubes)}, concepts {len(concepts)}, dimensions {len(modelDims)}, "
          f"members {len(members)}, table templates {len(tableTemplates)}")
    print(f"problems: {len(problems)}")
    for p in problems[:30]:
        print("  " + p)


def layoutOfSheet(ws):
    """The presentation of a template sheet: title, ordinate codes and grid headers.

    The column-ordinate row is the first row after the title whose cells are ordinate codes
    (``010`` …, or ``D01`` … for an open table's key columns); the cells above it, from the first
    header row, are the column headers (merged cells span columns or extend down).  Below it, the
    rows with a row code in the column left of the first data column are the table's rows; their
    labels are in column B, nested by the cell's indentation, with heading rows (no code)
    grouping the rows after them.  An open table has a single ``#`` row.  The row of the sheet
    labelled ``Hierarchy`` names each key column's dimension.
    """
    title = None
    for r in range(1, 12):
        v = ws.cell(r, 2).value
        if isinstance(v, str) and re.match(r"T\s?\d", v):
            title = re.sub(r"^T\s?[\d.]+\s*-\s*", "", v).strip()
            break
    codeRe = re.compile(r"^(\d{3}|D\d{2})$")
    idRow = None
    for r in range(6, min(ws.max_row, 40) + 1):
        codes = {c: str(ws.cell(r, c).value).strip() for c in range(3, ws.max_column + 1)
                 if ws.cell(r, c).value is not None and codeRe.match(str(ws.cell(r, c).value).strip())}
        if codes:
            idRow = r
            break
    if idRow is None:
        return None
    xCols = sorted(codes)
    xCodes = [codes[c] for c in xCols]
    firstDataCol = xCols[0]
    merged = [m.bounds for m in ws.merged_cells.ranges]       # (minCol, minRow, maxCol, maxRow)

    def mergedAt(r, c):
        for b in merged:
            if b[1] <= r <= b[3] and b[0] <= c <= b[2]:
                return b
        return None

    # column headers: the band above the ordinate row, down from the first row with a header
    top = None
    for r in range(idRow - 1, 5, -1):
        if any(ws.cell(r, c).value not in (None, "") for c in xCols):
            top = r
    xHeaders = []
    if top is not None:
        cells = []
        for r in range(top, idRow):
            for c in xCols:
                v = ws.cell(r, c).value
                if v in (None, ""):
                    continue
                b = mergedAt(r, c)
                spanCols = [x for x in xCols if (b[0] if b else c) <= x <= (b[2] if b else c)]
                cells.append((xCols.index(spanCols[0]), -(idRow - r), clean(v), r))
        cells.sort()
        xHeaders = withRollups([{"label": lbl, "labelLevel": -negLevel} for _, negLevel, lbl, _ in cells])

    # rows: codes in the column left of the first data column, labels in column B
    rowCodeCol = firstDataCol - 1
    yCodes, yHeaders = [], []
    lastRow = idRow
    for r in range(idRow + 1, ws.max_row + 1):
        v = ws.cell(r, rowCodeCol).value
        if v is not None and re.match(r"^\d{3}$", str(v).strip()):
            lastRow = r
    rowsInfo = []
    for r in range(idRow + 1, lastRow + 1):
        code = ws.cell(r, rowCodeCol).value
        code = str(code).strip() if code is not None and re.match(r"^\d{3}$", str(code).strip()) else None
        label = ws.cell(r, 2).value
        if code is None and label in (None, ""):
            continue
        indent = int(ws.cell(r, 2).alignment.indent or 0)
        rowsInfo.append((code, clean(label) if label else "", indent))
        if code:
            yCodes.append(code)
    if rowsInfo:
        maxIndent = max(i for _, _, i in rowsInfo)
        yHeaders = withRollups([{"label": lbl, "labelLevel": maxIndent - i + 1, "_own": code is not None}
                                for code, lbl, i in rowsInfo])

    keyHeaders = {}
    for r in range(idRow + 1, ws.max_row + 1):
        if str(ws.cell(r, 2).value or "").strip() == "Hierarchy":
            for c, code in zip(xCols, xCodes):
                if code.startswith("D"):
                    keyHeaders[code] = str(ws.cell(r, c).value or "")
            break
    return {"title": title, "xCodes": xCodes, "yCodes": yCodes, "xHeaders": xHeaders, "yHeaders": yHeaders,
            "keyHeaders": keyHeaders}


def withRollups(headers):
    """Mark the headers, in document order, that group the headers after them: a group with no row
    or column of its own is rollUpLocation "none", one with its own (a row code) is "start"."""
    for i, h in enumerate(headers):
        hasChildren = i + 1 < len(headers) and headers[i + 1]["labelLevel"] < h["labelLevel"]
        own = h.pop("_own", None)
        if hasChildren:
            h["rollUpLocation"] = "start" if own else "none"
        elif own is False:
            h["rollUpLocation"] = "none"   # a heading row with nothing beneath it
    return headers


def clean(v):
    return re.sub(r"\s+", " ", str(v)).strip()


# Base spec resource modules a compiled model contains rather than imports, merged by name as
# LoadLegacyTaxonomy._inlineBaseSpecObjects does.
STATUS_DATE = "2026"
BASE_SPEC_RESOURCES = ("xs-types.json", "core.json", "xbrlModel.json", "types.json", "xbrla.json",
                       "ref.json", "utr.json", "iso4217.json")


def inlineBaseSpecObjects(oim):
    resourcesDir = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "resources")
    model, namespaces = oim["xbrlModel"], oim["documentInfo"]["namespaces"]
    seen = {}
    for fname in BASE_SPEC_RESOURCES:
        try:
            with open(os.path.join(resourcesDir, fname), encoding="utf-8") as fh:
                base = json.load(fh)
        except (OSError, ValueError):
            continue
        for p, ns in (base.get("documentInfo", {}).get("namespaces") or {}).items():
            # the resource files are templates: their URIs carry the status-date placeholder
            namespaces.setdefault(p, ns.replace("((~status_date_uri~))", STATUS_DATE))
        for key, val in (base.get("xbrlModel") or {}).items():
            if not isinstance(val, list) or key in ("importedTaxonomies", "importMapping"):
                continue
            target = model.setdefault(key, [])
            keys = seen.setdefault(key, {o.get("name") or o.get("forObject") for o in target if isinstance(o, dict)})
            for obj in val:
                k = obj.get("name") or obj.get("forObject") if isinstance(obj, dict) else None
                if k is None or k not in keys:
                    target.append(obj)
                    if k is not None:
                        keys.add(k)


if __name__ == "__main__":
    main(sys.argv)
