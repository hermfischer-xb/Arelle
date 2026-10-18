# Copyright Exbee Ltd 2026
# License Apache 2
"""
dnbSampleReport.py -- a DNB xBRL-CSV report package as a Tavi report read by its fact sources

Purpose
-------
``tlbTemplate-dnb.py`` generates a compiled model of DNB's MES templates, keeping DNB's own names,
with a ``tableTemplate`` and ``factMap`` per template that match DNB's xBRL-CSV table files.  This
script makes a report of one of DNB's sample report packages: the model, plus a fact source for
each of the package's table files (``reports/t01.02.csv`` …), named in ``documentInfo.sourceMappings``.
Loading the report reads the CSV files through the PWD's table-template fact map; nothing in the
package is converted or copied.

A package's entity, reference period and currency are xBRL-CSV report parameters
(``reports/parameters.csv``: ``entityID``, ``refPeriod``, ``baseCurrency``).  The PWD's fact
source has no counterpart of xBRL-CSV's report parameters, so they are written into each table
template of the report as table-level ``factDimensions`` -- as literal values, the way the spec's
CSV example gives its entity -- and a monetary column's unit is the package's currency.  The
reference period, a date, is the instant at the end of that day.

The report covers its package's templates only: the model is cut down to the tables the package
has files for, since DNB's templates share data points across modules.

The result is one compiled document (the model's objects, the fact sources, the source mappings),
because a compiled model cannot be imported by a module without duplicating the base objects it
already contains.

Usage
-----
::

    python3 dnbSampleReport.py <model.json> <package directory or reports directory> <report.json>
"""
import csv
import json
import os
import sys
from datetime import date, timedelta


def main(argv):
    if len(argv) < 4:
        sys.exit(__doc__.split("Usage\n-----\n::\n")[1].strip())
    modelPath, packageDir, outPath = argv[1:4]
    reportsDir = packageDir
    for root, _dirs, files in os.walk(packageDir):
        if "parameters.csv" in files:
            reportsDir = root
            break
    with open(os.path.join(reportsDir, "parameters.csv"), newline="", encoding="utf-8-sig") as fh:
        parameters = {row["name"]: row["value"] for row in csv.DictReader(fh)}
    entity = parameters["entityID"]
    # refPeriod is a date; as an instant it is the end of that day (as in the xBRL-XML mapping)
    y, mo, d = (int(x) for x in parameters["refPeriod"].split("-"))
    period = (date(y, mo, d) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    currency = parameters.get("baseCurrency")

    with open(modelPath, encoding="utf-8") as fh:
        report = json.load(fh)
    model = report["xbrlModel"]
    templates = {t["name"]: t for t in model.get("tableTemplates", ())}
    factMaps = {f["name"]: f for f in model.get("factMaps", ())}
    tablePrefix = model["name"].partition(":")[0]

    outDir = os.path.dirname(os.path.abspath(outPath))
    sourceMappings, factSources, missing = [], [], []
    for fileName in sorted(os.listdir(reportsDir)):
        if not fileName.lower().startswith("t") or not fileName.lower().endswith(".csv"):
            continue                       # parameters.csv, FilingIndicators.csv
        table = fileName[:-4].upper()      # t01.02.csv -> T01.02
        factMapName = f"{tablePrefix}:{table}_FactMap"
        if factMapName not in factMaps:
            missing.append(fileName)
            continue
        sourceName = f"{tablePrefix}:{table}_Source"
        sourceMappings.append({"sourceName": sourceName,
                               "url": os.path.relpath(os.path.join(reportsDir, fileName), outDir).replace(os.sep, "/")})
        factSources.append({"name": sourceName, "factMapName": factMapName,
                            "cubeName": f"{tablePrefix}:{table}_Cube"})
        template = templates[factMaps[factMapName]["templateName"]]
        template["factDimensions"] = {"xbrl:entity": entity, "xbrl:period": period}
        if currency:
            for column in template["columns"]:
                fd = column.get("factDimensions") or {}
                if str(fd.get("xbrl:unit", "")).startswith("iso4217:"):
                    fd["xbrl:unit"] = currency

    # The report is its package's templates: the model is cut down to them (layouts, cubes and
    # their exclusion cubes, table templates and fact maps), as a filed report covers one module.
    # Templates of other modules share data points with these, and would otherwise show the
    # report's facts too.
    reported = {f"{tablePrefix}:{fs['name'].split(':', 1)[1].rsplit('_Source', 1)[0]}" for fs in factSources}
    for layout in model.get("layouts", ()):
        layout["dataTables"] = [t for t in layout.get("dataTables", ()) if t["name"] in reported]
    keepCubes = {f"{t}_Cube" for t in reported}
    for c in model.get("cubes", ()):
        if c["name"] in keepCubes:
            keepCubes.update(c.get("excludeCubes", ()))
    model["cubes"] = [c for c in model.get("cubes", ()) if c["name"] in keepCubes
                      or not c["name"].startswith(tablePrefix + ":")]
    model["tableTemplates"] = [t for t in model.get("tableTemplates", ()) if t["name"].rsplit("_Template", 1)[0] in reported]
    model["factMaps"] = [f for f in model.get("factMaps", ()) if f["name"].rsplit("_FactMap", 1)[0] in reported]
    usedDomains = {cd.get("domainNetwork") for c in model["cubes"] for cd in c.get("cubeDimensions", ())}
    for layout in model.get("layouts", ()):       # a key column's hierarchy (axis group network)
        for t in layout.get("dataTables", ()):
            for axis in ("xAxis", "yAxis", "zAxis"):
                for g in ((t.get(axis) or {}).get("gridAxis") or {}).get("axisGroups") or ():
                    usedDomains.add(g.get("axisNetwork"))
    model["domainNetworks"] = [d for d in model.get("domainNetworks", ()) if d["name"] in usedDomains
                               or not d["name"].startswith(tablePrefix + ":")]
    report["documentInfo"]["sourceMappings"] = sourceMappings
    model["name"] = f"{tablePrefix}:{os.path.basename(os.path.normpath(packageDir))}".replace(" ", "_")
    model["factSources"] = factSources
    with open(outPath, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(f"{len(factSources)} table files bound to fact sources; entity {entity}, period {period}"
          + (f"; no template for {missing}" if missing else ""))


if __name__ == "__main__":
    main(sys.argv)
