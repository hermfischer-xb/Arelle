"""
See COPYRIGHT.md for copyright information.
"""

from typing import TYPE_CHECKING, Optional, Any, Union, ClassVar, TypeAlias
from collections import defaultdict, OrderedDict
from decimal import Decimal
from arelle.ModelValue import QName, AnyURI
from ordered_set import OrderedSet
from arelle.plugin.XbrlModel.ModelValueMore import SQName
from arelle.plugin.XbrlModel.XbrlConcept import XbrlDataType
from .XbrlTypes import XbrlTaxonomyModelAlias,XbrlModuleAlias, QNameKeyType, DefaultFalse, DefaultTrue, OptionalList, OptionalDict, NonemptySet
from .XbrlObject import XbrlObject, XbrlReportObject
from .XbrlProperty import XbrlProperty
from .XbrlUnit import  parseUnitString
from .XbrlConst import xbrl as xbrlNs

# xbrl:nil property marks a fact whose value is not disclosed (oim-taxonomy "Nil Property").
qnFactNilProperty = QName("xbrl", xbrlNs, "nil")

XbrlFactAlias: TypeAlias = "XbrlFact"

def locatorPropertyView(locatorObj):
    """Property view of a source locator: its locator properties directly.

    Such an object is nothing but its properties, so the default view -- a "properties" node
    wrapping them -- costs the reader an extra expansion for no information. List-valued
    properties (e.g. the html element ids a value is sourced from) are joined, since a Python
    list repr reads poorly in a property grid.
    """
    return tuple((str(propObj.property),
                  ", ".join(str(v) for v in propObj.value)
                  if isinstance(propObj.value, (list, tuple, set, OrderedSet))
                  else str(propObj.value))
                 for propObj in locatorObj.properties or ())

XbrlValueSourceAlias: TypeAlias = "XbrlValueSource"
class XbrlSourceLocator(XbrlObject):
    """ Source Locator Object
        Reference: tavi#sec-source-locator

        One region of source content, identified by the locator properties of the fact locator
        type that applies (an html element id; a pdf page with a marked-content id; an html5
        element pointer with a text offset and quote).
    """
    valueSource: XbrlValueSourceAlias
    properties: OrderedSet[XbrlProperty] # (required) A set of property objects used to specify the interface properties defined by the locatorType for the fact source.

    @property
    def propertyView(self):
        return locatorPropertyView(self)

class XbrlValueSource(XbrlObject):
    """ Value Source Object
        Reference: tavi#sec-value-source

        The content from which the value of its fact is obtained: when the fact has no ``value``
        and is not nil, the value is the concatenation, in order, of the content its ``sources``
        identify, to which ``transformation``, ``scale`` and ``sign`` are then applied. When the
        fact has a ``value``, or is nil, it only identifies where the fact is displayed.
    """
    fact: XbrlFactAlias
    reportSource: Optional[QName] # (optional) The fact source object identifying the document the sources are in; when absent the single sourceMapping, or the document encapsulating the model, applies (tavi "File reference resolution").
    transformation: Optional[QName] # (optional) A transform object reference converting the concatenated source text into the value.
    scale: Optional[int] # (optional) A power of 10 by which the source number is multiplied (such as when shown in billions).
    sign: Optional[str] # (optional) "-" if the resulting numerical value is negative.
    sources: NonemptySet[XbrlSourceLocator] # (required) An ordered set of source locator objects, each identifying one region of the source content.

class XbrlFact(XbrlReportObject):
    """ Fact Object
        Reference: oim-taxonomy#fact-object

        A fact has at most one value. It carries value, decimals and language itself; where and
        how the value is obtained from a document is its optional ``valueSource``. Several
        reported values for the same dimensions are several fact objects (duplicate facts).
    """
    module: XbrlModuleAlias  # facts in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (equired if no extends) The name is a QName that uniquely identifies the factspace object.
    value: Optional[Any] # (optional) The value of the fact. This can be a numeric value, a string, or any other type of value that is valid for the fact.
    decimals: Optional[int] # An integer providing the value of the {decimals} property, or absent if the value is infinitely precise or not applicable (for nil or non-numeric facts).
    roundingMode: Optional[str] # (optional) How this value was obtained from the value it represents, `roundToNearest` or `truncation`. Where absent the rounding mode of the XBRL model object declaring the fact applies, which is how a report whose statements follow different conventions is represented.
    language: Optional[str] # (optional) The language of the fact value, specified using the BCP 47 standard language code (e.g., "en" for English, "fr" for French).
    valueSource: Optional[XbrlValueSource] # (optional) Where and how the value of the fact is obtained from an embedding or accompanying document.
    dimensions: OptionalDict[QName, Any] # (optional) A dimensions object with properties corresponding to the members of the {dimensions} property.
    qualifier: OptionalDict[QName, Any] # (optional) A fact qualifier object, that indicates additional dimensional members that are valid for the fact.
    properties: Optional[NonemptySet[XbrlProperty]] # (optional) an ordered set of property objects used to specify additional properties associated with the fact using the property object.
    extends: Optional[QName] # (required if no name property) Names the fact object whose properties this one adds to. An extending fact adds properties only (not value, decimals, language or valueSource). This property cannot be used in conjunction with the name property.
    isExtensible: Union[bool, DefaultTrue] # (optional) If set to false, the fact is non-extensible and no importing taxonomy may augment it using extends. If set to true or omitted, the fact may be extended. The default value is true.
    _propertyMap: ClassVar[dict[type,dict[str, str]]] = {}

    @property
    def sources(self):
        """The source locators of the fact's value source, or () when it has none."""
        return self.valueSource.sources if self.valueSource is not None else ()

    @property
    def isNil(self) -> bool:
        """True when the fact has the xbrl:nil property: it has no value, and any valueSources
        only locate where it is displayed."""
        return any(getattr(p, "property", None) == qnFactNilProperty for p in (self.properties or ()))

class XbrlFootnote(XbrlReportObject):
    """ Footnote Object
        Reference: oim-taxonomy#footnote-object
    """
    module: XbrlModuleAlias  # facts in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (required) The name is a QName that uniquely identifies the abstract object.
    forObjects: NonemptySet[QName] # (required) QNames of the fact objects associated with this footnote.
    content: Optional[str] # (optional) The content of the footnote.
    language: Optional[str] # (optional) The language of the footnote text, specified using the BCP 47 standard language code (e.g., "en" for English, "fr" for French).

XbrlFactSourceAlias: TypeAlias = "XbrlFactSource"
XbrlXMLTemplateMapAlias: TypeAlias = "XbrlXMLTemplateMap"
class XbrlNamespaceMap(XbrlObject):
    """ Namespace Map Object
        Reference: oim-taxonomy#namespacemap-object
    """
    parent: Union[XbrlFactSourceAlias, XbrlXMLTemplateMapAlias]
    fromNamespacePrefix: Optional[str] # (optional) The original namespace prefix that is being redirected. Must be declared in documentInfo.namespaces.
    toNamespacePrefix: str # (required) The new namespace prefix that the fromNamespacePrefix is being redirected to. Must be declared in documentInfo.namespaces.

XbrlFactSourceDimensionSummaryAlias: TypeAlias = "XbrlFactSourceDimensionSummary"
class XbrlFactSourceDimensionSummaryRange(XbrlObject):
    """ Fact Source Dimension Summary Range Object
        Reference: oim-taxonomy#factsourcedimensionsummaryrange-object
    """
    factSourceDimensionSummary: XbrlFactSourceDimensionSummaryAlias
    min: Any # (required) The minimum value for the dimension in the data source identified by the factSource object. For explicit dimensions, the minimum value is a QName that references a dimension member object defined in the taxonomy model. For typed dimensions, the minimum value is a typed value that is valid for the dimension.
    max: Any # (required) The maximum value for the dimension in the data source identified by the factSource object. For explicit dimensions, the maximum value is a QName that references a dimension member object defined in the taxonomy model. For typed dimensions, the maximum value is a typed value that is valid for the dimension.

XbrlFactSourceMetadataAlias: TypeAlias = "XbrlFactSourceMetadata"
class XbrlFactSourceDimensionSummary(XbrlObject):
    """ Fact Source Dimension Summary Object
        Reference: oim-taxonomy#factsourcedimensionsummary-object
    """
    factSourceMetadata: XbrlFactSourceMetadataAlias
    dimensionName: QName # (required) A QName that identifies the dimension.
    memberCount: int # (required) The number of unique members for the dimension in the data source identified by the factSource object. This can be used to determine the size of the data source and to optimize processing of the data source.
    isTyped: bool # (required) A boolean value that indicates whether the dimension is a typed dimension. This can be used to determine how to process the dimension values in the data source identified by the factSource object.
    sampleMembers: Optional[OrderedSet[Any]] # (optional) An ordered set of sample members for the dimension in the data source identified by the factSource object. This can be used to provide examples of the dimension values in the data source and to optimize processing of the data source. For explicit dimensions, the sample members are QNames that reference dimension member objects defined in the taxonomy model. For typed dimensions, the sample members are typed values that are valid for the dimension.
    memberRange: Optional[XbrlFactSourceDimensionSummaryRange] # (optional) A tuple that provides the range of members for the dimension in the data source identified by the factSource object. This can be used to provide information about the distribution of dimension values in the data source and to optimize processing of the data source. For explicit dimensions, the member range is a tuple of QNames that reference dimension member objects defined in the taxonomy model. For typed dimensions, the member range is a tuple of typed values that are valid for the dimension.

XbrlFactSourceAlias: TypeAlias = "XbrlFactSource"
class XbrlFactSourceMetadata(XbrlObject):
    """ Fact Source Metadata Object
        Reference: oim-taxonomy#factsourcemetadata-object
    """
    factSource: XbrlFactSourceAlias
    factCount: int # (required) The factCount property is the number of facts that are obtained from the data source identified by the factSource object. This can be used to determine the size of the data source and to optimize processing of the data source.
    validationTimestamp: str # (required) The validationTimestamp property is the date and time when the data source identified by the factSource object was last validated. This can be used to determine the freshness of the data source and to optimize processing of the data source.
    concepts: OrderedSet[QName] # (required) An ordered set of QNames that reference concept objects defined in the taxonomy model. If provided, the factSource object only applies to the facts with these concepts. If not provided, the factSource object applies to all facts in the report.
    dimensions: OrderedSet[XbrlFactSourceDimensionSummary] # (required) An ordered set of QNames that reference dimension objects defined in the taxonomy model. If provided, the factSource object only applies to the facts with these dimensions. If not provided, the factSource object applies to all facts inAn ordered set of factSourceDimensionSummary objects that describe the dimensions used in the fact collection, including the xbrl:concept dimension.
    threshold: Optional[int] # (optional) The threshold property is a numeric value that can be used to determine when to apply certain processing rules or optimizations to the data source identified by the factSource object. For example, if the factCount property exceeds the threshold value, certain processing rules or optimizations may be applied to improve performance.
    properties: Optional[NonemptySet[XbrlProperty]] # (optional) an ordered set of property objects used to specify additional properties associated with the factSource using the property object.

class XbrlFactSource(XbrlReportObject):
    """ Fact Source Object
        Reference: oim-taxonomy#factsource-object
    """
    module: XbrlModuleAlias  # table templates in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (required) The name is a QName that uniquely identifies the factSource object.
    factMapName: QName # (required) The fact map name is a QName that references a factMap object defined in the taxonomy model.
    cubeName: Optional[QName] # (optional) A QName that references a cube object defined in the taxonomy model. If provided, the fact source object only applies to the facts with this cube. If not provided, the fact source object applies to all facts in the report.
    namespaceMaps: OptionalList[XbrlNamespaceMap] # (optional) An array of namespaceMap objects that maps the namespace of the model defined in a datasource to the namespace of an updated model. This MUST only used with an XBRL data source.
    factNames: Optional[OrderedSet[QName]] # (optional) An ordered set of QNames that reference fact objects defined in the taxonomy model. If provided, the factSource object only applies to the facts with these names. If not provided, the factSource object applies to all facts in the report.
    hash: Optional[str] # (optional) A hash value that can be used to verify the integrity of the data source. The specific hashing algorithm used is not defined in the specification and may be determined by the implementation.
    metadata: Optional[XbrlFactSourceMetadata] # (optional) A metadata object that can contain any additional information about the fact source. The structure and content of the metadata object is not defined in the specification and may be determined by the implementation.
    factIdentifierNamespacePrefix: Optional[str] # (optional) Namespace prefix (declared in documentInfo.namespaces) used for generated fact object names derived from this source; if absent the model's namespace prefix is used.
    properties: Optional[NonemptySet[XbrlProperty]] # (optional) an ordered set of property objects used to specify additional properties associated with the factSource using the property object.

class XbrlTableTemplate(XbrlReportObject):
    """ Table Template Object
        Reference: oim-taxonomy#tabletemplate-object
    """
    module: XbrlModuleAlias  # table templates in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (required) The name is a QName that uniquely identifies the tableTemplate object.
    rowIdColumn: Optional[str] # (optional) An identifier specifying the name of the row ID column.
    columns: dict # (required) A columns object. (See xbrl-csv specification)
    factDimensions: dict[QName, Any] # (required) A dimensions object that defines table dimensions. (See xbrl-csv specification)
    decimals: Optional[Decimal] # (optional) A decimals val
    extends: Optional[QName] # (required if no name property) Names the tableTemplate object that is appended to. The items in the table template with this property are appended to the end of the columns target table template object. This property cannot be used in conjunction with the name and rowIdColumn property.
    isExtensible: Union[bool, DefaultTrue] # (optional) If set to false, the table template is non-extensible and no importing taxonomy may augment it using extends. If set to true or omitted, the table template may be extended. The default value is true.

class XbrlJSONTemplateMap(XbrlReportObject):
    """ JSON Template Map Object
        Reference: oim-taxonomy#jsontemplatemap-object
    """
    module: XbrlModuleAlias  # table templates in taxonomy module are owned by the txmyMdl
    name: Optional[QNameKeyType] # (optional) The QName that identifies the JSON template map object.
    factDimensions: dict[QName, Any] # (required) A factDimensions object that defines map dimensions.
    valuePath: str # (required) A JSONPath expression that identifies the location of the fact values in the JSON data.
    decimals: Optional[Decimal] # (optional) A decimals value (xs:decimal).

class XbrlXMLTemplateMap(XbrlReportObject):
    module: XbrlModuleAlias  # table templates in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (required) The QName that identifies the XML template map object.
    factDimensions: dict[QName, Any] # (required) A factDimensions object that defines map dimensions.
    valuePath: str # (required) A JSONPath expression that identifies the location of the fact values in the JSON data.
    decimals: Optional[Decimal] # (optional) A decimals value (xs:decimal).
    namespaceMap: Optional[XbrlNamespaceMap] # (optional) A namespace mapping object that defines the namespace prefixes used in the XPath expression.

class XbrlFactMap(XbrlReportObject):
    module: XbrlModuleAlias  # table templates in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (required) The name is a QName that uniquely identifies the fact map object.
    factLocatorType: Optional[QName] # (required if valueSources used) QName referencing an XbrlFactLocatorType that defines the locator interface for fact value sources.
    templateName: Optional[QName] # (optional) A QName that references a tableTemplate, jsonTemplateMap or xmlTemplateMap object defined in the taxonomy model. If provided, the fact map object only applies to the facts with this template. If not provided, the fact map object applies to all facts in the report.

class XbrlFactLocatorType(XbrlReportObject):
    module: XbrlModuleAlias  # fact locator types in taxonomy module are owned by the txmyMdl
    name: QNameKeyType # (required) The name is a QName that uniquely identifies this locator type.
    sourceMediaType: str # (optional) Advisory MIME type or media format hint (e.g. text/html, application/pdf, text/csv). Informational only; does not restrict which source documents a locator of this type may reference.
    requiredProperties: Optional[NonemptySet[QName]] # (optional) A set of property QNames that MUST be present on any factValueSource object using this type.
    allowedProperties: Optional[NonemptySet[QName]] # (optional) A set of property QNames that MAY be present on any factValueSource object using this type. If not provided, there are no restrictions on the properties that may be used with this locator type.

# XbrlReport object has been removed: facts, footnotes, factSources, tableTemplates etc.
# are owned directly by XbrlModule per the current oim-taxonomy specification.