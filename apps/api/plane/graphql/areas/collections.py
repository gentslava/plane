# OVERLAY: mobile-graphql — resolver module for the "collections" area.
#
# Collections (a.k.a. wiki page collections: CollectionType, CollectionPageType,
# CollectionMemberType) are a Plane Cloud / Enterprise-Edition feature. The native
# app talks to the same SDL contract against Cloud and against self-hosted, so the
# Cloud SDL declares these Query/Mutation fields and object types — but the OSS
# (self-hosted community) backend this gateway runs on ships NO Collection,
# CollectionMember or CollectionPage model / table.
#
#   $ grep -ril Collection apps/api/plane/db/models/   ->  (nothing)
#   $ grep -ril Collection apps/api/plane/app/views/   ->  (only "collection.abc" /
#                                                           unrelated state code)
#
# There is therefore no REST view or queryset to replicate: the ground-truth
# implementation simply does not exist on this distribution. To keep the WHOLE
# schema buildable (every name in @query.field / @mutation.field must be a real
# SDL field, every ObjectType a real SDL type) and to never crash the app with a
# "Cannot return null for a non-nullable field" error, every field is bound to a
# safe, contract-honoring default:
#
#   * list fields ([Type!]!)            -> [] (empty list, still non-null)
#   * paginator fields (...Response!)   -> _page([], cursor) (empty page)
#   * boolean mutations (Boolean!)      -> False (nothing removed/added)
#   * non-null singletons (Type!) and   -> raise a clear GraphQLError("…not
#     persisting mutations                 available on this instance"). Returning
#                                          None there is a non-null contract
#                                          violation; "not found / unavailable" is
#                                          the correct, honest behavior when the
#                                          entity genuinely cannot be resolved.
#
# Auth is enforced exactly like the rest of the gateway (`_user(info)`); an
# unauthenticated caller gets the same empty/None shape so we never leak the
# difference between "unauthenticated" and "feature absent".
#
# The smart snake_case fallback resolver (see schema.py / resolver_utils.py) would
# resolve every CollectionType / CollectionPageType / CollectionMemberType field
# from a model instance automatically — so no per-field ObjectType binding is
# needed here (we never produce such an instance). The ObjectType objects are
# still created and exported so that, the day a Collection model lands on this
# distribution, computed/M2M/FK fields can be attached in one place without
# touching the schema wiring.
from ariadne import MutationType, ObjectType, QueryType
from graphql import GraphQLError

from plane.graphql.resolvers import _page, _user

query = QueryType()
mutation = MutationType()

# ObjectType handles for every Collection* type in the SDL. They carry no explicit
# field resolvers today (no backing model -> nothing to compute); the global
# snake_case fallback covers them. Kept + exported as the single attach point for
# future computed / M2M-id / nested-FK fields (e.g. pageCount, isOwner,
# currentUserAccess on CollectionType; projects[], isShared on CollectionPageType).
collection_type = ObjectType("CollectionType")
collection_page_type = ObjectType("CollectionPageType")
collection_member_type = ObjectType("CollectionMemberType")


def _feature_unavailable(name):
    """Raised for non-null singletons / persisting mutations that cannot be honored
    because this self-hosted distribution has no Collection backing model."""
    return GraphQLError(
        f"'{name}' is not available on this instance: collections are a "
        "Plane Cloud / Enterprise feature and have no backing model on this "
        "self-hosted deployment."
    )


# --- Queries -------------------------------------------------------------------------


@query.field("collections")
def resolve_collections(_, info, slug, cursor=None):
    # SDL: collections(slug, cursor) -> CollectionTypePaginatorResponse!
    # No Collection model -> always an empty, well-formed page (auth still gates it).
    user = _user(info)
    if user is None:
        return _page([], cursor)
    return _page([], cursor)


@query.field("collection")
def resolve_collection(_, info, slug, collection):
    # SDL: collection(slug, collection) -> CollectionType! (non-null singleton).
    # Cannot fabricate a CollectionType; honor non-null by signalling "not found".
    user = _user(info)
    if user is None:
        raise _feature_unavailable("collection")
    raise _feature_unavailable("collection")


@query.field("collectionSearch")
def resolve_collection_search(_, info, slug, query):
    # SDL: collectionSearch(slug, query) -> [CollectionType!]!
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("collectionPages")
def resolve_collection_pages(_, info, slug, collection, cursor=None):
    # SDL: collectionPages(slug, collection, cursor) -> CollectionPageTypePaginatorResponse!
    user = _user(info)
    if user is None:
        return _page([], cursor)
    return _page([], cursor)


@query.field("collectionPage")
def resolve_collection_page(_, info, slug, collection, page):
    # SDL: collectionPage(slug, collection, page) -> CollectionPageType! (non-null).
    user = _user(info)
    if user is None:
        raise _feature_unavailable("collectionPage")
    raise _feature_unavailable("collectionPage")


@query.field("collectionMembers")
def resolve_collection_members(_, info, slug, collection):
    # SDL: collectionMembers(slug, collection) -> [CollectionMemberType!]!
    user = _user(info)
    if user is None:
        return []
    return []


# --- Mutations -----------------------------------------------------------------------


@mutation.field("createCollection")
def resolve_create_collection(_, info, slug, input):
    # SDL: createCollection(slug, input: CollectionCreateInput!) -> CollectionType!
    # Persisting requires a Collection model that does not exist here.
    user = _user(info)
    if user is None:
        raise _feature_unavailable("createCollection")
    raise _feature_unavailable("createCollection")


@mutation.field("updateCollection")
def resolve_update_collection(_, info, slug, collection, input):
    # SDL: updateCollection(slug, collection, input: CollectionUpdateInput!) -> CollectionType!
    user = _user(info)
    if user is None:
        raise _feature_unavailable("updateCollection")
    raise _feature_unavailable("updateCollection")


@mutation.field("deleteCollection")
def resolve_delete_collection(_, info, slug, collection):
    # SDL: deleteCollection(slug, collection) -> Boolean!
    # Nothing to delete (no model) -> safe False, no crash.
    user = _user(info)
    if user is None:
        return False
    return False


@mutation.field("createCollectionPage")
def resolve_create_collection_page(_, info, slug, collection, input):
    # SDL: createCollectionPage(slug, collection, input: CollectionPageCreateInput!) -> CollectionPageType!
    user = _user(info)
    if user is None:
        raise _feature_unavailable("createCollectionPage")
    raise _feature_unavailable("createCollectionPage")


@mutation.field("addCollectionPages")
def resolve_add_collection_pages(_, info, slug, collection, input):
    # SDL: addCollectionPages(slug, collection, input: CollectionPageAddInput!) -> [CollectionPageType!]!
    # No collection -> nothing is added; return empty (non-null) list.
    user = _user(info)
    if user is None:
        return []
    return []


@mutation.field("removeCollectionPages")
def resolve_remove_collection_pages(_, info, slug, collection, pageIds=None, input=None):
    # SDL: removeCollectionPages(slug, collection, pageIds, input) -> Boolean!
    user = _user(info)
    if user is None:
        return False
    return False


@mutation.field("addCollectionMember")
def resolve_add_collection_member(_, info, slug, collection, input):
    # SDL: addCollectionMember(slug, collection, input: CollectionMemberAddInput!) -> CollectionMemberType!
    user = _user(info)
    if user is None:
        raise _feature_unavailable("addCollectionMember")
    raise _feature_unavailable("addCollectionMember")


@mutation.field("updateCollectionMember")
def resolve_update_collection_member(_, info, slug, collection, collectionMember, input):
    # SDL: updateCollectionMember(slug, collection, collectionMember, input: CollectionMemberUpdateInput!) -> CollectionMemberType!
    user = _user(info)
    if user is None:
        raise _feature_unavailable("updateCollectionMember")
    raise _feature_unavailable("updateCollectionMember")


@mutation.field("removeCollectionMember")
def resolve_remove_collection_member(_, info, slug, collection, collectionMember):
    # SDL: removeCollectionMember(slug, collection, collectionMember) -> Boolean!
    user = _user(info)
    if user is None:
        return False
    return False


BINDABLES = [query, mutation, collection_type, collection_page_type, collection_member_type]
