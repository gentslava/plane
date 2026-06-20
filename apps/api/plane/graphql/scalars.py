# OVERLAY: mobile-graphql — custom scalar (de)serialisers used by the Cloud SDL.
import datetime
import uuid

from ariadne import ScalarType

datetime_scalar = ScalarType("DateTime")
date_scalar = ScalarType("Date")
json_scalar = ScalarType("JSON")
uuid_scalar = ScalarType("UUID")
void_scalar = ScalarType("Void")
# Override the built-in ID scalar: graphql-core's default rejects UUID values, so
# coerce every id (UUID, int, str) to its string form. Lets model-backed id fields
# resolve via the snake_case fallback without a per-field resolver.
id_scalar = ScalarType("ID")


@id_scalar.serializer
def serialize_id(value):
    return None if value is None else str(value)


@datetime_scalar.serializer
def serialize_datetime(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


@date_scalar.serializer
def serialize_date(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


@json_scalar.serializer
def serialize_json(value):
    return value


@json_scalar.value_parser
def parse_json(value):
    return value


@uuid_scalar.serializer
def serialize_uuid(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


@uuid_scalar.value_parser
def parse_uuid(value):
    return value


@void_scalar.serializer
def serialize_void(value):
    return None


SCALARS = [datetime_scalar, date_scalar, json_scalar, uuid_scalar, void_scalar, id_scalar]
