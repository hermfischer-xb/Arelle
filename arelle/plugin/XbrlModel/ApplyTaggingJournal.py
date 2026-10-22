'''
See COPYRIGHT.md for copyright information.

Applies a tagging journal produced by the ixbrl-viewer's tagger back to a model.

The viewer's tagger writes nothing to the model or the document: its only output is a
*journal* of the value-source decisions a user made. That keeps the non-mutation invariant
mechanically true rather than merely intended, and leaves applying the journal as a separate
step, which is this module.

Two parties run that step, and they want different things:

* a **preparer**, tagging a report they are authoring. The bindings they make are their own
  content -- the filing says where its values come from -- so the journal is applied INTO THE
  MODEL. The result is a filing with no derived content.
An entry's `previous` field is not used. The viewer documents it as carrying the sources a
rebind displaced, and as what makes an entry reversible, but it is currently always null there
-- every entry looks like a first bind -- so nothing here may depend on it.

* a **disseminator**, tagging a report somebody else filed: re-rendering a prior filing onto a
  surface it was never tagged against (an N-CSR too unwieldy to read as XHTML, laid out as
  PDF), or locating values for a viewer. Those bindings are not the filer's content, so they
  are applied as DERIVED CONTENT -- derived fact values with a `basis` of `bound`,
  beside a model that is left exactly as filed.

The distinction is not a processing option; it is which party is running the tool, and it
decides what the artifact claims. `--taggingJournalInto` says which.

For a preparer there is a second choice, which the model already distinguishes and which
`--taggingValueAuthority` selects: whether the DOCUMENT text is authoritative (the fact carries
value sources and no value, and any consumer re-derives the value from the document) or the
VALUE is (the fact carries the value it was given -- from an accounting system, a prior
filing, a spreadsheet -- and the binding is an anchor that merely locates it). Both are
faithful; they differ in what the filing asserts is the point of truth.
'''
import io, json, os

from arelle.ModelValue import QName

from .XbrlConst import xbrl as xbrlNs
from .XbrlFact import XbrlFactValueSource
from .XbrlProperty import XbrlProperty

# Version 2: entries name the bound occurrence by factName (a fact is one occurrence since the
# fact value object was collapsed into the fact); version 1 named it by factValueName.
JOURNAL_VERSION = 2

INTO_MODEL = "model"
INTO_DERIVED = "derivedContent"

AUTHORITY_DOCUMENT = "document"
AUTHORITY_VALUE = "value"

qnHtmlElementId = QName("xbrl", xbrlNs, "htmlElementId")


def loadJournal(cntlr, path):
    """Read and sanity-check a tagging journal. Returns the parsed object, or None."""
    try:
        with io.open(path, "rt", encoding="utf-8") as fh:
            journal = json.load(fh)
    except (IOError, OSError, ValueError) as ex:
        cntlr.addToLog(_("Could not read the tagging journal %(file)s: %(error)s"),
                       messageArgs={"file": path, "error": ex},
                       messageCode="arelle:taggingJournalUnreadable", level="ERROR")
        return None
    version = journal.get("journalVersion")
    if version != JOURNAL_VERSION:
        cntlr.addToLog(_("Tagging journal %(file)s is version %(found)s; this processor "
                         "applies version %(expected)s."),
                       messageArgs={"file": path, "found": version, "expected": JOURNAL_VERSION},
                       messageCode="arelle:taggingJournalVersion", level="ERROR")
        return None
    return journal


def _factIndexes(compMdl):
    """Two ways to find the fact a journal entry names, most reliable first.

    `factName` is the model's own name for the OCCURRENCE being bound. Since the fact value
    object was collapsed into the fact, a fact is one occurrence in the document -- a data point
    printed in four places is four facts with the same dimensions -- so naming the fact attaches
    an entry to exactly one occurrence, with no choice for this applier to make. (Journal
    version 1 named the occurrence by `factValueName`; it is refused, see JOURNAL_VERSION.)

    The html element id is the fallback for an entry that names its fact only by the id the
    viewer keyed it under. That works for a located fact and not otherwise, which is why the
    name was added.
    """
    byFactName, byElementId = {}, {}
    for module in compMdl.xbrlModels.values():
        for fact in getattr(module, "facts", None) or ():
            factName = getattr(fact, "name", None)
            if factName is not None:
                byFactName[str(factName)] = fact
            for source in fact.valueSources or ():
                for prop in getattr(source, "properties", None) or ():
                    if getattr(prop, "property", None) == qnHtmlElementId:
                        value = prop.value
                        for elementId in (value if isinstance(value, (list, tuple)) else [value]):
                            byElementId.setdefault(str(elementId), fact)
    return byFactName, byElementId


def _entryFactKey(factId):
    """The adapter key a journal entry's factId carries, without the report index.

    Only used for an entry that carries no factName. The viewer forms a fact
    id as "<reportIndex>-<key>"; the key is the html element id for a located fact, and a
    synthetic "pf-N" / "hf-N" for one it placed on a PDF or could not locate -- a position in
    the order the adapter built, not an identity. That is why the model's own names are
    preferred, and why they were added.
    """
    text = str(factId or "")
    prefix, sep, rest = text.partition("-")
    return rest if (sep and prefix.isdigit()) else text


def _sourceObjectsFrom(entry, fact):
    """The journal's `sources` as XbrlFactValueSource objects of `fact`.

    The tagger already writes them in fact-value-source form -- properties with a property
    QName and a collection-typed value -- so this is an attach, not a translation.
    """
    sources = []
    for sourceDict in entry.get("sources") or ():
        sourceObj = XbrlFactValueSource()
        sourceObj.fact = fact
        sourceObj.properties = []
        for propDict in sourceDict.get("properties") or ():
            propObj = XbrlProperty()
            propObj.property = propDict.get("property")
            propObj.value = propDict.get("value")
            sourceObj.properties.append(propObj)
        sources.append(sourceObj)
    return sources


def applyJournal(cntlr, compMdl, journal, into=INTO_DERIVED, authority=AUTHORITY_DOCUMENT):
    """Apply a tagging journal. Returns (appliedCount, unresolved) and, for the derived-content
       party, leaves the bindings on compMdl for SaveModel to publish."""
    byFactName, byElementId = _factIndexes(compMdl)
    applied, unresolved, bound = 0, [], []
    for entry in journal.get("entries") or ():
        if entry.get("op") != "bindValueSource":
            unresolved.append((entry.get("factId"), "unsupported op {}".format(entry.get("op"))))
            continue
        # resolve by the model's own name where the entry carries it; the element-id
        # fallback is for an entry that does not
        found = None
        named = entry.get("factName")
        if named:
            found = byFactName.get(str(named))
        else:
            key = _entryFactKey(entry.get("factId"))
            named = key
            found = byElementId.get(key)
        if found is None:
            unresolved.append((entry.get("factId"),
                               "the model has no fact named '{}'".format(named)))
            continue
        fact = found
        if into == INTO_MODEL:
            # the preparer's own content: the binding becomes the model's
            fact.valueSources = _sourceObjectsFrom(entry, fact)
            if authority == AUTHORITY_VALUE:
                # the value is the point of truth and the value sources only locate it
                fact.value = entry.get("factValue")
            else:
                # the document is the point of truth: the value sources supply the value
                fact.value = None
                derivation = entry.get("derivation") or {}
                for name in ("scale", "sign", "transformation"):
                    if derivation.get(name) is not None:
                        setattr(fact, name, derivation[name])
        else:
            # somebody else's report: the binding is ours, not theirs
            bound.append({"factName": str(fact.name),
                          "value": entry.get("factValue"),
                          "sourceText": entry.get("capturedText"),
                          "sources": entry.get("sources")})
        applied += 1
    if into == INTO_DERIVED and bound:
        compMdl._boundFactValues = bound
    return applied, unresolved
