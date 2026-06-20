# OVERLAY: mobile-graphql — build the executable schema from the captured Cloud SDL.
#
# The SDL (schema.graphql) is the authoritative contract, pulled via introspection
# from Plane Cloud's api.plane.so/graphql/. We bind resolvers for the operations the
# app uses; every other field resolves via snake_case_fallback_resolvers
# (camelCase GraphQL field -> snake_case model attribute / dict key).
import os

from ariadne import make_executable_schema

from .areas import BINDABLES as AREA_BINDABLES
from .mutations import BINDABLES as MUTATION_BINDABLES
from .resolver_utils import install_safe_query_defaults, install_smart_fallback
from .resolvers import RESOLVERS
from .scalars import SCALARS

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.graphql")

with open(SCHEMA_PATH, encoding="utf-8") as fh:
    type_defs = fh.read()

# AREA_BINDABLES first so the established core RESOLVERS/MUTATION_BINDABLES win on the
# few fields an area also binds (epic mutations, initiativesCount); new area fields apply.
schema = make_executable_schema(type_defs, *AREA_BINDABLES, *RESOLVERS, *MUTATION_BINDABLES, *SCALARS)

# Explicit resolvers (RESOLVERS) win; everything else is covered generically:
#   1. unbound root fields -> safe empty value (no non-null crashes)
#   2. all other object fields -> smart camelCase/FK-aware attribute access
install_safe_query_defaults(schema)
install_smart_fallback(schema)
