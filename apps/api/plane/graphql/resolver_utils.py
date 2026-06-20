# OVERLAY: mobile-graphql — generic resolution engine.
#
# Two pieces let us cover the large Cloud schema without a resolver per field:
#   * smart_default_resolver — camelCase->snake_case attribute access that also
#     turns Django FK objects into their id for ID-typed fields (via the *_id
#     column, so no extra query), and any model into its pk for a scalar field.
#   * install_safe_query_defaults — every root Query/Mutation field we have NOT
#     implemented gets a type-appropriate empty value (list -> [], paginator ->
#     empty page, scalar -> zero/""/false, nullable -> None) so the app never
#     hits a "Cannot return null for non-nullable field" and never hard-blocks.
import collections.abc

from django.db import models
from graphql import (
    GraphQLID,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLScalarType,
)

_MISSING = object()


def camel_to_snake(name):
    out = []
    for index, char in enumerate(name):
        if char.isupper() and index > 0:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def _named_type(gql_type):
    while isinstance(gql_type, (GraphQLNonNull, GraphQLList)):
        gql_type = gql_type.of_type
    return gql_type


def _is_list(gql_type):
    while isinstance(gql_type, GraphQLNonNull):
        gql_type = gql_type.of_type
    return isinstance(gql_type, GraphQLList)


def empty_paginator():
    # Valid Plane cursor format ("limit:page:is_prev"); an empty cursor leaves the
    # app's pagination state machine stuck (infinite loader) on a cold start.
    return {
        "prev_cursor": "100:-1:0",
        "cursor": "100:0:0",
        "next_cursor": None,
        "prev_page_results": False,
        "next_page_results": False,
        "count": 0,
        "total_count": 0,
        "results": [],
    }


def smart_default_resolver(parent, info, **kwargs):
    field_name = info.field_name
    snake = camel_to_snake(field_name)

    if isinstance(parent, collections.abc.Mapping):
        if snake in parent:
            return parent[snake]
        return parent.get(field_name)

    named = _named_type(info.return_type)

    # ID-typed scalar field backed by a FK: use the *_id column (no DB join).
    if named is GraphQLID and not _is_list(info.return_type):
        fk_attr = snake + "_id"
        if hasattr(parent, fk_attr):
            return getattr(parent, fk_attr)

    value = getattr(parent, snake, _MISSING)
    if value is _MISSING:
        return None

    # A model where a scalar is expected (ID/String/...) -> its primary key.
    if isinstance(value, models.Model) and isinstance(named, GraphQLScalarType):
        return value.pk

    # A Django related manager (M2M / reverse FK) for a list field -> materialise.
    if _is_list(info.return_type) and hasattr(value, "all") and callable(getattr(value, "all", None)):
        rows = list(value.all())
        # [ID!] / scalar list -> primary keys; [ObjectType!] -> the objects.
        if not isinstance(named, GraphQLObjectType):
            return [row.pk for row in rows]
        return rows

    return value


_SCALAR_EMPTY = {
    "Int": 0,
    "Float": 0.0,
    "Boolean": False,
    "String": "",
    "ID": "",
}


def _safe_value(gql_type):
    if isinstance(gql_type, GraphQLNonNull):
        inner = gql_type.of_type
        if isinstance(inner, GraphQLList):
            return []
        if isinstance(inner, GraphQLScalarType):
            return _SCALAR_EMPTY.get(inner.name)
        if isinstance(inner, GraphQLObjectType) and inner.name.endswith("PaginatorResponse"):
            return empty_paginator()
        # Other non-null objects (aggregates) need an explicit resolver.
        return None
    if isinstance(gql_type, GraphQLList):
        return []
    return None


def _const_resolver(value):
    def resolver(parent, info, **kwargs):
        return value

    return resolver


def install_safe_query_defaults(schema):
    """Give every still-unbound root Query/Mutation field a safe empty value."""
    for root in (schema.query_type, schema.mutation_type):
        if root is None:
            continue
        for field in root.fields.values():
            if field.resolve is None:
                field.resolve = _const_resolver(_safe_value(field.type))


def install_smart_fallback(schema):
    """Set the smart resolver on every object field that has no explicit resolver."""
    for gql_type in schema.type_map.values():
        if not isinstance(gql_type, GraphQLObjectType) or gql_type.name.startswith("__"):
            continue
        for field in gql_type.fields.values():
            if field.resolve is None:
                field.resolve = smart_default_resolver
