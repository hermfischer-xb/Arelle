"""
See COPYRIGHT.md for copyright information.

Fact-value resolution and locator-property registry.

A fact may provide its value in two ways:

1. **Literal**: a ``value`` property whose string is validated against the
   concept's data type. This is the legacy / OIM-JSON path.
2. **External**: a ``valueSource`` whose ``sources`` (source locator objects) use
   locator properties (e.g. ``xbrl:htmlElementId``,
   ``xbrl:htmlDataAttribute``, PDF ``page`` + ``mcid``, tabular ``tabularPath``)
   to point at content of an external source document (HTML / PDF / tabular).

When a fact has a literal ``value``, its ``valueSource`` only locates the corresponding
content: they are validated here, but the text they resolve to is not the fact's value.

The ``factLocatorType`` is resolved via the spec chain: fact.valueSource.reportSource →
factSource → factMap → factMap.factLocatorType, identifying an
``XbrlFactLocatorType`` object that describes:

  * which locator properties are required (``requiredProperties``)
  * which locator properties are permitted (``allowedProperties``)
  * the source media type (``sourceMediaType`` — ``text/html``,
    ``application/pdf``, ``text/csv`` ...)

This module exposes:

  * ``LocatorPropertyRegistry`` - a model-scoped lookup of
    ``XbrlFactLocatorType`` definitions.
  * ``valueSourceResolver`` registry mapping ``sourceMediaType`` strings to
    backend resolver callables (HTML, PDF, tabular, ...).
  * ``validateAndResolveValueSources(...)`` - the single chokepoint called
    from ``ValidateFacts.resolveFact`` to (a) validate locator properties and
    (b) resolve external content into a string usable by ``validateValue``.

Resolvers themselves are lazy: an HTML resolver does not open the source
document until ``resolve()`` is actually called, mirroring the
``FactSourceLoader`` lazy pattern from step 3. When the source document is
unavailable (common in conformance suites that test structural validity in
isolation) the resolver returns ``None`` and the caller treats the value as
deferred -- structural errors are still raised but data-type validation of
the (missing) value is skipped.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, Tuple, TYPE_CHECKING

from arelle.ModelValue import QName, qname
from .XbrlConst import xbrl as _XBRL_NS

if TYPE_CHECKING:
    from .XbrlFact import XbrlFact, XbrlSourceLocator, XbrlFactLocatorType
    from .XbrlModel import XbrlCompiledModel


# OIM transformation namespace (xbrltt:*); transform-types.json loads the
# definitions; the actual transformation functions live in arelle.FunctionIxt
# under the legacy inline-XBRL transformation registry namespaces. We reuse
# the v5 registry (which is a superset of v4) for xbrltt:* localNames.
# Derived from the current xbrl namespace so it stays in step with the spec year.
_XBRLTT_NS = _XBRL_NS + "/transform-types"


# --------------------------------------------------------------------
# Transformation application (reuses arelle.FunctionIxt registry)
# --------------------------------------------------------------------

def applyTransformation(transformQn: Optional[QName], text: str, compMdl=None) -> str:
    """Apply an inline-XBRL or OIM transformation to ``text`` and return the
    transformed string. Returns ``text`` unchanged when no transformation is
    given or when the transformation is unknown / fails.

    This is a thin wrapper over ``arelle.FunctionIxt.ixtNamespaceFunctions``
    (the same registry the legacy inline-XBRL evaluator uses). For OIM
    ``xbrltt:*`` transformations we map onto the v5 ixt table, which uses
    matching hyphenated local names (e.g. ``num-dot-decimal``).

    A registry that is NOT built in -- SEC's ``ixt-sec:*`` -- is contributed by a plugin through
    the ``ModelManager.LoadCustomTransforms`` hook, which is how the EDGAR transform plugin
    supplies boolballotbox, numwordsen, duryear and the rest. Core's own inline evaluator falls
    back to ``modelManager.customTransforms`` for exactly this (ModelInstanceObject), and so must
    this: without it a US filing's value is silently left as its untransformed document text --
    "☒" where xs:boolean is expected -- which then fails its datatype for a reason that points
    at the value rather than at the missing transform.
    """
    if transformQn is None or text is None:
        return text
    from arelle.FunctionIxt import ixtNamespaceFunctions, ixtNamespaces
    ns = getattr(transformQn, "namespaceURI", None)
    ln = getattr(transformQn, "localName", None)
    fnTable = ixtNamespaceFunctions.get(ns)
    if fnTable is None and ns == _XBRLTT_NS:
        fnTable = ixtNamespaceFunctions.get(ixtNamespaces["ixt v5"])
    fn = fnTable.get(ln) if fnTable else None
    if fn is None:
        fn = _customTransform(transformQn, compMdl)
    if fn is None:
        return text
    try:
        return fn(text)
    except Exception:
        return text


def _customTransform(transformQn, compMdl):
    """A plugin-contributed transformation function for ``transformQn``, or None.

    The registry is built lazily by ModelManager.loadCustomTransforms() from the
    ``ModelManager.LoadCustomTransforms`` hook, so it is empty until something asks for it.
    """
    modelManager = getattr(getattr(compMdl, "modelXbrl", compMdl), "modelManager", None)
    if modelManager is None:
        return None
    try:
        modelManager.loadCustomTransforms()
        return (modelManager.customTransforms or {}).get(transformQn)
    except Exception:
        return None


def _applyScaleAndSign(text: Optional[str],
                       scale: Optional[int],
                       sign: Optional[str]) -> Optional[str]:
    """Apply ``valueSource.scale`` (power of 10) and ``valueSource.sign`` to a
    post-transformation numeric string. Returns ``text`` unchanged when both
    are absent or when ``text`` is not numeric.

    Per spec these adjustments are properties of ``valueSource`` resolution
    only (html / pdf / tabular) -- they are NOT applied to literal
    ``fact.value`` strings.
    """
    if text is None:
        return text
    hasScale = scale not in (None, 0)
    flipSign = (sign == "-")
    if not hasScale and not flipSign:
        return text
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(str(text))
    except (InvalidOperation, ValueError, TypeError):
        return text
    if hasScale:
        try:
            d = d * (Decimal(10) ** int(scale))
        except (InvalidOperation, ValueError, TypeError):
            return text
    if flipSign:
        d = -d
    # Normalize away exponent for integral magnitudes (e.g. 359241 * 10^6 ->
    # "359241000000" rather than "3.59241E+11").
    if d == d.to_integral_value():
        d = d.quantize(Decimal(1))
    return format(d, "f")


# --------------------------------------------------------------------
# Media-type resolver registry
# --------------------------------------------------------------------

#: Resolver signature: ``(factValueSource, locatorType, fact, compMdl)``
#: returns the textual value extracted from the external source, or ``None``
#: if the source is not currently accessible (treated as "deferred"; the
#: caller skips data-type validation of the value but still raises any
#: structural errors that have already been detected).
ResolverFn = Callable[..., Optional[str]]

_RESOLVERS: Dict[str, ResolverFn] = {}


def registerValueSourceResolver(mediaType: str, fn: ResolverFn) -> None:
    """Register a backend resolver for a given source media type."""
    _RESOLVERS[mediaType] = fn


def getValueSourceResolver(mediaType: Optional[str]) -> Optional[ResolverFn]:
    if mediaType is None:
        return None
    return _RESOLVERS.get(mediaType)


# --------------------------------------------------------------------
# LocatorPropertyRegistry
# --------------------------------------------------------------------

class LocatorPropertyRegistry:
    """Caches ``XbrlFactLocatorType`` definitions per compiled model.

    Used to answer "for this ``factLocatorType`` QName, which properties
    must / may appear on a ``valueSource.properties`` list?" in O(1) per
    fact, without re-walking the taxonomy.
    """

    __slots__ = ("_byName",)

    def __init__(self, compMdl: "XbrlCompiledModel") -> None:
        from .XbrlFact import XbrlFactLocatorType  # avoid import cycle
        self._byName: Dict[QName, "XbrlFactLocatorType"] = {}
        for module in compMdl.xbrlModels.values():
            for locType in getattr(module, "factLocatorTypes", None) or ():
                if isinstance(locType, XbrlFactLocatorType):
                    self._byName[locType.name] = locType
        for obj in compMdl.filterNamedObjects(XbrlFactLocatorType):
            self._byName.setdefault(obj.name, obj)

    def get(self, locatorTypeQn: Optional[QName]) -> Optional["XbrlFactLocatorType"]:
        if locatorTypeQn is None:
            return None
        return self._byName.get(locatorTypeQn)


def _registryFor(compMdl: "XbrlCompiledModel") -> LocatorPropertyRegistry:
    reg = getattr(compMdl, "_locatorPropertyRegistry", None)
    if reg is None:
        reg = LocatorPropertyRegistry(compMdl)
        compMdl._locatorPropertyRegistry = reg
    return reg


# --------------------------------------------------------------------
# factLocatorType resolution
# --------------------------------------------------------------------
#
# Per spec (tavi.md "Value source object constraints"), when a fact has a valueSource,
# the factLocatorType is found through this chain:
#
#   fact.valueSource.reportSource (or implied from sourceMappings)
#     → factSource object (matched by sourceMappings[*].sourceName)
#       → factSource.factMapName → factMap object
#         → factMap.factLocatorType → XbrlFactLocatorType object
#
# The XbrlFactLocatorType defines requiredProperties, allowedProperties,
# and sourceMediaType for the value sources.
# --------------------------------------------------------------------

def _resolveFactLocatorType(
    fact: "XbrlFact",
    compMdl: "XbrlCompiledModel",
) -> Tuple[Optional[QName], Optional[QName]]:
    """Resolve the factLocatorType QName for a fact that has a valueSource.

    Follows the spec chain: fact.valueSource.reportSource → factSource → factMap → factLocatorType.

    Returns ``(factLocatorTypeQn, sourceQn)`` where factLocatorTypeQn is the
    QName of the XbrlFactLocatorType, or None if resolution fails.
    """
    from .XbrlFact import XbrlFactSource, XbrlFactMap

    sourceQn = fact.valueSource.reportSource if fact.valueSource is not None else None

    # If no explicit source, find default from sourceMappings
    if sourceQn is None:
        for module in compMdl.xbrlModels.values():
            for mapping in getattr(module, "_sourceMappings", None) or ():
                mappingSource = getattr(mapping, "sourceName", None)
                if mappingSource is not None:
                    sourceQn = mappingSource
                    break
            if sourceQn is not None:
                break

    if sourceQn is None:
        return None, None

    # Find the factSource object
    factSourceObj = compMdl.namedObjects.get(sourceQn)
    if not isinstance(factSourceObj, XbrlFactSource):
        return None, sourceQn

    # Follow factSource.factMapName → factMap
    factMapQn = factSourceObj.factMapName
    factMapObj = compMdl.namedObjects.get(factMapQn)
    if not isinstance(factMapObj, XbrlFactMap):
        return None, sourceQn

    # factMap.factLocatorType is the answer
    return factMapObj.factLocatorType, sourceQn


# --------------------------------------------------------------------
# Validation + resolution
# --------------------------------------------------------------------

def validateAndResolveValueSources(
    compMdl: "XbrlCompiledModel",
    fact: "XbrlFact",
) -> Tuple[bool, Optional[str]]:
    """Validate locator structure and (where possible) resolve external text.

    Returns ``(deferred, resolvedText)``:

      * ``deferred == True`` means the value is unresolvable in the current
        context (source document not present, resolver registered but media
        not accessible, etc.) and the caller should NOT run data-type
        validation against ``fact.value`` for this fact.
      * ``resolvedText`` is the extracted source text on success; ``None``
        otherwise.

    Errors raised here use these OIM-taxonomy codes:

      * ``oimte:factValueLocatorRequiredForValueSources`` - fact uses
        a ``valueSource`` but no ``factLocatorType`` could be resolved via
        the source → factSource → factMap → factLocatorType chain.
      * ``oimte:invalidQNameReference`` - ``factLocatorType`` QName does not
        resolve to a ``XbrlFactLocatorType`` object.
      * ``oimte:invalidObjectType`` - the referenced object is not a
        ``XbrlFactLocatorType``.
      * ``oimte:missingRequiredProperty`` - a property listed in
        ``locatorType.requiredProperties`` was not provided.
      * ``oimte:disallowedObjectProperty`` - a property was provided that is
        not in ``locatorType.allowedProperties`` (when that list is set).
    """
    valueSource = fact.valueSource
    valueSources = valueSource.sources if valueSource is not None else ()
    if not valueSources:
        return False, None

    # Collect all sourceMappings across modules for source/url lookups.
    allMappings: list = []
    for module in compMdl.xbrlModels.values():
        allMappings.extend(getattr(module, "_sourceMappings", None) or ())

    # ---- source QName validation -------------------------------------------
    factValueSourceQn = valueSource.reportSource
    if factValueSourceQn is not None:
        if not any(getattr(m, "sourceName", None) == factValueSourceQn for m in allMappings):
            compMdl.error(
                "oimte:invalidQNameReference",
                _("Fact %(fact)s source %(src)s does not match any "
                  "documentInfo.sourceMappings entry."),
                xbrlObject=fact,
                fact=getattr(fact, "name", None),
                src=factValueSourceQn,
            )
            return True, None
    elif len(allMappings) > 1:
        # No source on the fact and multiple mappings -> ambiguous.
        compMdl.error(
            "oimte:factSourceResolutionFailed",
            _("Fact %(fact)s does not specify a source and "
              "documentInfo.sourceMappings contains more than one entry."),
            xbrlObject=fact,
            fact=getattr(fact, "name", None),
        )
        return True, None

    # ---- transformation QName validation -----------------------------------
    # A transformation reference may resolve to:
    #   * a transformation object in the loaded taxonomy (xbrltt:* per
    #     transform-types.json or extension-namespace transforms), or
    #   * a legacy inline-XBRL transformation registry function (ixt v1-v5),
    #     which is registered in arelle.FunctionIxt.ixtNamespaceFunctions and
    #     reusable here without re-implementing the transform tables.
    transformQn = valueSource.transformation
    if transformQn is not None:
        ns = getattr(transformQn, "namespaceURI", None)
        ln = getattr(transformQn, "localName", None)
        from arelle.FunctionIxt import ixtNamespaceFunctions, ixtNamespaces
        v5Table = ixtNamespaceFunctions.get(ixtNamespaces["ixt v5"], {})
        validTransform = (
            transformQn in getattr(compMdl, "namedObjects", {})
            or (ns in ixtNamespaceFunctions
                and ln in ixtNamespaceFunctions[ns])
            or (ns == _XBRLTT_NS and ln in v5Table)
        )
        if not validTransform:
            compMdl.error(
                "oimte:invalidQNameReference",
                _("Fact %(fact)s transformation %(t)s does not "
                  "resolve to a known transformation."),
                xbrlObject=fact,
                fact=getattr(fact, "name", None),
                t=transformQn,
            )
            # Don't early-return; structural locator checks still apply.

    locatorTypeQn, _sourceQn = _resolveFactLocatorType(fact, compMdl)
    if locatorTypeQn is None:
        compMdl.error(
            "oimte:factValueLocatorRequiredForValueSources",
            _("Fact %(fact)s has a valueSource but no factLocatorType "
              "could be resolved via source → factSource → factMap → factLocatorType."),
            xbrlObject=fact,
            fact=getattr(fact, "name", None),
        )
        return True, None

    registry = _registryFor(compMdl)
    locatorType = registry.get(locatorTypeQn)
    if locatorType is None:
        if locatorTypeQn in getattr(compMdl, "namedObjects", {}):
            compMdl.error(
                "oimte:invalidObjectType",
                _("Fact %(fact)s factLocatorType %(name)s does not "
                  "reference a factLocatorType object."),
                xbrlObject=fact,
                fact=getattr(fact, "name", None),
                name=locatorTypeQn,
            )
        else:
            compMdl.error(
                "oimte:invalidQNameReference",
                _("Fact %(fact)s factLocatorType %(name)s does not "
                  "resolve to any factLocatorType object."),
                xbrlObject=fact,
                fact=getattr(fact, "name", None),
                name=locatorTypeQn,
            )
        return True, None

    required = set(getattr(locatorType, "requiredProperties", None) or ())
    allowed = set(getattr(locatorType, "allowedProperties", None) or ())
    allowed |= required  # required implies allowed

    # tavi "Value source object": the value is the concatenation, in order, of the content each
    # source locator identifies, to which transformation, scale and sign are then applied. It is
    # resolved only if every fragment is; a partial concatenation would be a different value.
    texts = []
    allResolved = True

    for source in valueSources:
        propNames = {
            getattr(p, "property", None) for p in (getattr(source, "properties", None) or ())
        }
        propNames.discard(None)

        missing = required - propNames
        if missing:
            compMdl.error(
                "oimte:missingRequiredProperty",
                _("Fact %(fact)s valueSource is missing required "
                  "properties %(missing)s for factLocatorType %(loc)s."),
                xbrlObject=fact,
                fact=getattr(fact, "name", None),
                missing=", ".join(str(p) for p in sorted(missing, key=str)),
                loc=locatorTypeQn,
            )

        if allowed:
            disallowed = propNames - allowed
            if disallowed:
                compMdl.error(
                    "oimte:disallowedObjectProperty",
                    _("Fact %(fact)s valueSource has disallowed "
                      "properties %(extra)s for factLocatorType %(loc)s."),
                    xbrlObject=fact,
                    fact=getattr(fact, "name", None),
                    extra=", ".join(str(p) for p in sorted(disallowed, key=str)),
                    loc=locatorTypeQn,
                )

        # Try to actually resolve content via the registered backend.
        mediaType = getattr(locatorType, "sourceMediaType", None)
        resolver = getValueSourceResolver(mediaType)
        if resolver is not None:
            try:
                text = resolver(source, locatorType, fact, compMdl)
            except Exception as e:  # resolver failures must never crash validation
                compMdl.warning(
                    "arelle:factValueResolverFailed",
                    _("Resolver for media type %(mt)s raised %(err)s; treating as deferred."),
                    mt=mediaType,
                    err=str(e),
                )
                text = None
            if text is None:
                allResolved = False
            else:
                texts.append(text)
        else:
            allResolved = False

    if not allResolved or not texts:
        return True, None
    # Apply the transformation (reusing the ixt registry from arelle.FunctionIxt -- the same
    # functions legacy iXBRL uses), then scale (power of 10) and sign. Per spec these apply only
    # to a value obtained from the sources, never to a literal fact.value.
    resolvedText = applyTransformation(transformQn, "".join(texts), compMdl)
    resolvedText = _applyScaleAndSign(resolvedText, valueSource.scale, valueSource.sign)
    return False, resolvedText


# --------------------------------------------------------------------
# Source URL resolution (FileSource-based)
# --------------------------------------------------------------------

def _resolveSourceUrl(fact, source, compMdl) -> Optional[str]:
    """Find the URL associated with a fact's source.

    Looks up ``fact.valueSource.reportSource`` (or the source-property on the
    valueSource itself) against each module's parsed ``_sourceMappings``
    list (built at load time from ``documentInfo.sourceMappings``).
    Returns the absolute or repo-relative URL string, or ``None`` if the
    fact does not designate any source mapping.
    """
    sourceQn = (fact.valueSource.reportSource if fact.valueSource is not None else None) or getattr(source, "source", None)
    for module in compMdl.xbrlModels.values():
        mappings = getattr(module, "_sourceMappings", None) or ()
        for mapping in mappings:
            if sourceQn is None or getattr(mapping, "sourceName", None) == sourceQn:
                url = getattr(mapping, "url", None)
                if url:
                    return url
    return None


def _propertyValues(source) -> Dict[Any, Any]:
    """Return ``{propertyQName: value}`` for the properties on a
    factValueSource. ``value`` may be a list (per the spec) or a scalar."""
    out: Dict[Any, Any] = {}
    for p in getattr(source, "properties", None) or ():
        qn = getattr(p, "property", None)
        if qn is None:
            continue
        v = getattr(p, "value", None)
        out[qn] = v
        # also key by localName for tolerant lookup ("htmlElementId" vs "xbrl:htmlElementId")
        ln = getattr(qn, "localName", None)
        if ln:
            out.setdefault(ln, v)
    return out


def _firstValue(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else None
    return str(v)


# --------------------------------------------------------------------
# Built-in resolvers
# --------------------------------------------------------------------

def _htmlDocFor(compMdl, url) -> Optional[Tuple[Any, Dict[str, Any]]]:
    """Parse the HTML source at ``url`` once per model and cache it on ``compMdl``.

    Returns ``(doc, idMap)`` -- the parsed document and an ``id -> element`` map -- or
    ``None`` when the document cannot be read/parsed. Resolving an inline report
    resolves every fact against the same document, so parsing once (and indexing ids
    once) turns O(facts) full parses into a single parse; ``None`` results are cached
    too, so an unreadable source is not retried per fact.
    """
    cache = getattr(compMdl, "_htmlDocCache", None)
    if cache is None:
        cache = compMdl._htmlDocCache = {}
    if url in cache:
        return cache[url]
    parsed: Optional[Tuple[Any, Dict[str, Any]]] = None
    try:
        from lxml import html as lxml_html
    except ImportError:
        cache[url] = None
        return None
    try:
        # lxml.html.fromstring rejects unicode strings that carry an XML encoding
        # declaration, so always read the source as bytes.
        f = compMdl.fileSource.file(url, binary=True)[0]
        try:
            content = f.read()
        finally:
            try:
                f.close()
            except Exception:
                pass
        doc = lxml_html.fromstring(content)
        idMap: Dict[str, Any] = {}
        for el in doc.iter():
            elId = el.get("id")
            if elId and elId not in idMap:
                idMap[elId] = el
        parsed = (doc, idMap)
    except Exception:
        parsed = None
    cache[url] = parsed
    return parsed


def _resolveHtmlValueSource(source, locatorType, fact, compMdl) -> Optional[str]:
    """HTML resolver: locate the configured element(s) in the source document
    and return their text content.

    Supports the standard locator properties:

      * ``xbrl:htmlElementId`` -- element by ``id``
      * ``xbrl:htmlElementPointer`` -- element by XPointer element() child sequence, for
        an element without an id (e.g. one displaying a hidden fact through a
        hidden-style property)
      * ``xbrl:htmlDataAttribute`` -- element by ``data-*`` attribute lookup
        (value may be ``"attrName"`` or ``"attrName=attrValue"``)

    Returns ``None`` when:

      * no ``sourceMappings`` URL is bound to this fact, or
      * the URL is not accessible via the model's FileSource, or
      * none of the locator properties match an element in the document.

    Callers treat ``None`` as "deferred" and skip data-type validation.
    """
    url = _resolveSourceUrl(fact, source, compMdl)
    if not url:
        return None
    props = _propertyValues(source)
    # A report entry point loaded from an archive binds its sourceMapping to the ARCHIVE so that
    # the report package's catalog remappings apply and the whole document set is discovered. An
    # archive cannot be read as a document, so resolving against the mapping URL alone left every
    # value of such a report unresolved -- silently, since an unresolvable source is "deferred".
    # reportDocumentUrls yields the document(s) the entry point named; trying each in turn also
    # gives a multi-document report the document that actually holds the locator.
    from .FactPipeline import reportDocumentUrls
    for docUrl in reportDocumentUrls(compMdl, url):
        if not _fileSourceCanRead(compMdl, docUrl):
            continue
        parsed = _htmlDocFor(compMdl, docUrl)
        if parsed is None:
            continue
        doc, idMap = parsed
        # element-id lookup (O(1) against the cached id map)
        v = _firstValue(props.get("htmlElementId"))
        if v:
            el = idMap.get(v)
            if el is not None:
                return el.text_content()
        # element-pointer lookup
        pointer = _firstValue(props.get("htmlElementPointer"))
        if pointer:
            el = _resolveElementPointer(compMdl, docUrl, pointer)
            if el is not None:
                return "".join(el.itertext())
        # data-attribute lookup
        dataAttr = _firstValue(props.get("htmlDataAttribute"))
        if dataAttr:
            if "=" in dataAttr:
                attrName, _, attrVal = dataAttr.partition("=")
                xpath = f"//*[@{attrName}={_xpathLiteral(attrVal)}]"
            else:
                xpath = f"//*[@{dataAttr}]"
            try:
                matches = doc.xpath(xpath)
            except Exception:
                matches = []
            if matches:
                return matches[0].text_content()
    return None


def _resolveElementPointer(compMdl, url, pointer):
    """Resolve an ``xbrl:htmlElementPointer`` against ``url``, or None.

    A child sequence counts element children, so it must be resolved against the same
    tree structure it was built on. An inline document is XHTML and the importer builds
    pointers on its XML tree; an HTML parser may restructure the markup (libxml2 closes a
    ``<p>`` at a block element) and silently resolve to a different element. So the
    document is parsed as XML here, falling back to the HTML parse only when it is not
    well-formed XML. Parsed once per URL.
    """
    from .HtmlElementPointer import buildIdIndex, resolvePointer
    cache = getattr(compMdl, "_xmlDocCache", None)
    if cache is None:
        cache = compMdl._xmlDocCache = {}
    if url not in cache:
        parsed = None
        try:
            from lxml import etree
            f = compMdl.fileSource.file(url, binary=True)[0]
            try:
                root = etree.fromstring(f.read())
            finally:
                f.close()
            parsed = (root, buildIdIndex(root))
        except Exception:
            htmlParsed = _htmlDocFor(compMdl, url)
            if htmlParsed is not None:
                parsed = (htmlParsed[0], buildIdIndex(htmlParsed[0]))
        cache[url] = parsed
    parsed = cache[url]
    if parsed is None:
        return None
    return resolvePointer(pointer, parsed[0], parsed[1])


def _xpathLiteral(s: str) -> str:
    """Quote an XPath string literal, handling embedded quotes."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


def _fileSourceCanRead(compMdl, url: str) -> bool:
    try:
        fs = compMdl.fileSource
        return bool(fs is not None and fs.exists(url))
    except Exception:
        return False


registerValueSourceResolver("text/html", _resolveHtmlValueSource)
registerValueSourceResolver("html", _resolveHtmlValueSource)
# xbrl:xhtmlElementLocatorType (the inline fact maps): ids resolve the same way; element
# pointers are resolved against the XML parse (_resolveElementPointer).
registerValueSourceResolver("application/xhtml+xml", _resolveHtmlValueSource)


_pdfExtractorCache: Dict[str, Any] = {}


def _resolvePdfValueSource(source, locatorType, fact, compMdl) -> Optional[str]:
    """PDF resolver: delegate to ``PdfTextExtractor`` for the three standard
    PDF locator properties (form field, mcid+page, structure-element id).

    Returns ``None`` when the companion PDF is not currently readable or when
    none of the locator properties matched.
    """
    url = _resolveSourceUrl(fact, source, compMdl)
    if not url:
        return None
    if not _fileSourceCanRead(compMdl, url):
        return None
    # Resolve URL to a local filepath usable by pikepdf.
    try:
        filepath = compMdl.fileSource.url(url) if hasattr(compMdl.fileSource, "url") else url
    except Exception:
        filepath = url
    extractor = _pdfExtractorCache.get(filepath)
    if extractor is None:
        from .PdfTextExtractor import PdfTextExtractor
        extractor = PdfTextExtractor(filepath)
        _pdfExtractorCache[filepath] = extractor

    props = _propertyValues(source)
    # Form field
    fieldName = _firstValue(props.get("pdfFormField"))
    if fieldName:
        text = extractor.textByFormField(fieldName)
        if text is not None:
            return text
    # MCID + page
    mcid = _firstValue(props.get("pdfMcid"))
    page = _firstValue(props.get("pdfPage"))
    if mcid is not None and page is not None:
        try:
            text = extractor.textByMcid(int(page), int(mcid))
        except (TypeError, ValueError):
            text = None
        if text is not None:
            return text
    # Structure element ID
    elemId = _firstValue(props.get("pdfElementId"))
    if elemId:
        text = extractor.textByStructElemId(elemId)
        if text is not None:
            return text
    return None


registerValueSourceResolver("application/pdf", _resolvePdfValueSource)
registerValueSourceResolver("pdf", _resolvePdfValueSource)

